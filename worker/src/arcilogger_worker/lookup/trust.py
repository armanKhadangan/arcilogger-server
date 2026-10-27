"""Which log rows may be evidence: the sender trust and usable-row rules (ADR-0016).

A leaf module: of this project it imports only :mod:`arcilogger_db.models` and
:mod:`arcilogger_worker.lookup.types`, so the lease timeline, the lookup, the alerts and the
API's source health can all import it without an import cycle (the lookup never needs the lease
timeline's DHCP source registry for it).

**Trusted senders.** An unconfigured sender is filed as ``unknown-<ip>`` and parsed by content:
every line with ``logid=`` goes to the FortiGate parser, so any host that can send syslog could
otherwise create FortiGate lease evidence or sessions. :func:`untrusted_sources` names the
sources whose ``fg_*`` rows (``fg_dhcp``, ``fg_traffic``, the ``fg_event`` rows a lookup probes)
are never lease evidence and are marked wherever a lookup lists them; it never filters ``mt_*``
rows, which only configured MikroTik sources write (content detection never says MikroTik).

**Usable FortiGate DHCP rows.** :func:`refusal` is the one implementation of the rules that
decide whether an ``fg_dhcp`` row is lease evidence, checked in the order of
:data:`REFUSAL_RULES`; :func:`kept` is its complement. The lease job, the lease alerts, the
"Lease evidence" card and the lookup all count with it, so they can never disagree.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Collection
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement, Text, cast, select
from sqlalchemy.orm import Session

from arcilogger_db.models import Source
from arcilogger_worker.lookup.types import LookupSettings

UNKNOWN_PREFIX: Final = "unknown-"
#: Why an ``fg_dhcp`` row is not lease evidence, in the order the rules are checked; a row is
#: counted under the first rule that applies.
UNTRUSTED_SOURCE: Final = "fortigate_dhcp_untrusted_source"
EXCLUDED: Final = "fortigate_dhcp_excluded"
IPV6: Final = "ipv6_ignored"
UNMAPPED: Final = "fortigate_dhcp_unmapped"
UNEXPECTED_MSG: Final = "fortigate_dhcp_unexpected_msg"
RECEIVE_TIME: Final = "fortigate_dhcp_receive_time"
BAD_MAC: Final = "fortigate_dhcp_bad_mac"
REFUSAL_RULES: Final = (
    UNTRUSTED_SOURCE,
    EXCLUDED,
    IPV6,
    UNMAPPED,
    UNEXPECTED_MSG,
    RECEIVE_TIME,
    BAD_MAC,
)
#: The rules that are configuration, not refusals (an interface left out on purpose, IPv6 out
#: of scope): counted apart, never a refusal in an alert or on the card.
CONFIGURATION: Final = frozenset({EXCLUDED, IPV6})


def untrusted_sources(session: Session) -> frozenset[str]:
    """The sources whose ``fg_*`` rows are not trusted: an unconfigured sender (``unknown``) or
    a source of another type than ``fortigate``.

    One unconfigured sender is trusted after all: an ``unknown-<ip>`` source whose address lies
    in the address (an address or a network) of an **enabled**, configured ``fortigate`` source
    — a FortiGate whose syslog sender address changed (another interface or ``source-ip``, an
    HA member sending from its own address) and was configured afterwards. Anyone who can forge
    that address can forge the configured source as well, so trusting its earlier lines adds
    no path. A removed (disabled) source vouches for nothing, but keeps its own trust: it keeps
    its type and ``unknown = false``, so the lines it sent while configured keep counting.
    Rows of a source without a ``sources`` row (test helpers only; ingest writes the row with
    the rows) count as trusted. Loaded once per run or lookup.
    """
    rows = session.execute(
        select(Source.name, Source.type, Source.unknown, Source.enabled, cast(Source.address, Text))
    ).all()
    vouching = [
        ipaddress.ip_interface(address).network
        for _name, kind, unknown, enabled, address in rows
        if enabled and not unknown and kind == "fortigate" and address is not None
    ]
    untrusted = set()
    for name, kind, unknown, _enabled, address in rows:
        if not unknown and kind == "fortigate":
            continue
        sender = _sender(name, address) if unknown else None
        if sender is not None and any(sender in network for network in vouching):
            continue
        untrusted.add(name)
    return frozenset(untrusted)


def _sender(name: str, address: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address of an ``unknown-<ip>`` source: its row's, else the one in its name (the
    collector writes an IPv6 address with ``-`` for ``:``)."""
    if address is not None:
        return ipaddress.ip_interface(address).ip
    if not name.startswith(UNKNOWN_PREFIX):
        return None
    text = name.removeprefix(UNKNOWN_PREFIX)
    for candidate in (text, text.replace("-", ":")):
        try:
            return ipaddress.ip_address(candidate)
        except ValueError:
            continue
    return None


def refusal(settings: LookupSettings, untrusted: Collection[str]) -> ColumnElement[str | None]:
    """The code of the first rule of :data:`REFUSAL_RULES` that refuses an ``fg_dhcp`` row, as
    a SQL ``CASE`` over its columns; ``NULL`` for a usable row."""
    raise NotImplementedError("needs the fg_dhcp table")


def kept(settings: LookupSettings, untrusted: Collection[str]) -> ColumnElement[bool]:
    """``refusal(...) IS NULL``: the ``fg_dhcp`` rows that are lease evidence."""
    raise NotImplementedError("needs the fg_dhcp table")


def usable_dhcp_origins(
    session: Session,
    settings: LookupSettings,
    untrusted: Collection[str],
    subnets: Collection[str],
    lo: datetime,
    hi: datetime,
) -> dict[str, set[tuple[str, str | None]]]:
    """Per configured subnet, the DHCP origins ``(table, vd)`` of usable rows in ``[lo, hi]``:
    ``mt_dhcp`` rows with action ``assigned``/``deassigned`` (never filtered by ``untrusted``)
    and the ``fg_dhcp`` rows :func:`kept` accepts. Two origins in one subnet are two DHCP
    authorities."""
    raise NotImplementedError("needs the fg_dhcp table")


def srcmac_ip_counts(
    session: Session,
    low: datetime,
    high: datetime,
    interfaces: Collection[str] | None,
    untrusted: Collection[str],
    macs: Collection[str] | None = None,
) -> dict[tuple[str, str], int]:
    """Per ``(srcintf, srcmac)``, the distinct IPv4 source addresses of trusted ``fg_traffic``
    rows in ``[low, high]`` (``interfaces`` ``None``: every interface; ``macs``: only these):
    the one query of the lookup's router guard, the srcmac alert and the observed endpoint."""
    raise NotImplementedError("needs fg_traffic.srcmac")
