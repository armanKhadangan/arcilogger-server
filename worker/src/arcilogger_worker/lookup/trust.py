"""Which log rows may be evidence: the sender trust and usable-row rules (ADR-0016).

A leaf module: of this project it imports only :mod:`arcilogger_db.models`,
:mod:`arcilogger_worker.lookup.types` and the table names of :mod:`arcilogger_worker.lookup.refs`
(itself a leaf), so the lease timeline, the lookup, the alerts and the API's source health can all
import it without an import cycle (the lookup never needs the lease timeline's DHCP source
registry for it).

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

from sqlalchemy import ColumnElement, Text, case, cast, distinct, func, null, select, true
from sqlalchemy.dialects.postgresql import ARRAY, CIDR
from sqlalchemy.orm import Session

from arcilogger_db.models import FgDhcp, FgTraffic, MtDhcp, Source
from arcilogger_worker.lookup.refs import DHCP_TABLE, FG_DHCP_TABLE
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
#: Never lease evidence besides multicast (broadcast is multicast too).
ZERO_MAC: Final = "00:00:00:00:00:00"
#: The second hex digit of a MAC whose first octet has the multicast bit set.
_ODD_HEX: Final = ("1", "3", "5", "7", "9", "b", "d", "f")


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
    a SQL ``CASE`` over its columns; ``NULL`` for a usable row.

    1. a sender in ``untrusted`` (:func:`untrusted_sources`);
    2. an interface of ``dhcp.fortigate_interfaces_excluded``;
    3. an IPv6 address;
    4. a logid ``dhcp.fortigate_logids`` does not map (every logid while the map is empty);
    5. a ``dhcp_msg`` (``NULL`` included) that ``dhcp.fortigate_dhcp_msgs`` does not list for
       its logid, compared case-insensitively (a logid without an entry is not restricted);
    6. the receive time instead of the FortiGate's event time (``device_time = false``);
    7. a zero, broadcast or multicast MAC address (the least significant bit of the first octet
       set; ``macaddr`` prints lower-case hex, so its second character is odd).
    """
    rules: list[tuple[ColumnElement[bool], str]] = []
    if untrusted:
        rules.append((FgDhcp.source.in_(sorted(untrusted)), UNTRUSTED_SOURCE))
    if settings.fortigate_interfaces_excluded:
        rules.append(
            (FgDhcp.interface.in_(sorted(settings.fortigate_interfaces_excluded)), EXCLUDED)
        )
    rules.append((func.family(FgDhcp.ip) == 6, IPV6))
    logids = sorted(settings.fortigate_logids)
    rules.append(
        (FgDhcp.logid.is_(None) | FgDhcp.logid.not_in(logids) if logids else true(), UNMAPPED)
    )
    for logid, messages in sorted(settings.fortigate_dhcp_msgs.items()):
        allowed = sorted({message.lower() for message in messages})
        rules.append(
            (
                (FgDhcp.logid == logid)
                & (FgDhcp.dhcp_msg.is_(None) | func.lower(FgDhcp.dhcp_msg).not_in(allowed)),
                UNEXPECTED_MSG,
            )
        )
    rules.append((FgDhcp.device_time.is_(False), RECEIVE_TIME))
    rules.append(
        (
            (FgDhcp.mac == ZERO_MAC) | func.substr(cast(FgDhcp.mac, Text), 2, 1).in_(_ODD_HEX),
            BAD_MAC,
        )
    )
    return case(*rules, else_=null())


def kept(settings: LookupSettings, untrusted: Collection[str]) -> ColumnElement[bool]:
    """``refusal(...) IS NULL``: the ``fg_dhcp`` rows that are lease evidence."""
    return refusal(settings, untrusted).is_(None)


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
    authorities. Every subnet of ``subnets`` is a key (an empty set: no usable row); one query
    per table."""
    origins: dict[str, set[tuple[str, str | None]]] = {subnet: set() for subnet in subnets}
    if not origins:
        return origins
    given = {str(ipaddress.ip_network(subnet, strict=False)): subnet for subnet in origins}
    nets = select(func.unnest(cast(sorted(given), ARRAY(CIDR))).label("subnet")).subquery("nets")
    mikrotik = (
        select(cast(nets.c.subnet, Text))
        .distinct()
        .join_from(nets, MtDhcp, MtDhcp.ip.op("<<=")(nets.c.subnet))
        .where(
            MtDhcp.time >= lo,
            MtDhcp.time <= hi,
            MtDhcp.action.in_(("assigned", "deassigned")),
        )
    )
    for (subnet,) in session.execute(mikrotik):
        origins[given[str(ipaddress.ip_network(subnet))]].add((DHCP_TABLE, None))
    fortigate = (
        select(cast(nets.c.subnet, Text), FgDhcp.extra["vd"].astext)
        .distinct()
        .join_from(nets, FgDhcp, FgDhcp.ip.op("<<=")(nets.c.subnet))
        .where(FgDhcp.time >= lo, FgDhcp.time <= hi, kept(settings, untrusted))
    )
    for subnet, vd in session.execute(fortigate):
        origins[given[str(ipaddress.ip_network(subnet))]].add((FG_DHCP_TABLE, vd))
    return origins


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
    the one query of the lookup's router guard, the srcmac alert and the observed endpoint.

    IPv6 sources are not counted (a dual-stack client uses IPv6 and temporary IPv6 addresses
    with the same MAC), nor rows without an interface; ``untrusted`` senders' rows never count.
    """
    if (interfaces is not None and not interfaces) or (macs is not None and not macs):
        return {}
    query = (
        select(
            FgTraffic.srcintf,
            cast(FgTraffic.srcmac, Text),
            func.count(distinct(FgTraffic.srcip)),
        )
        .where(
            FgTraffic.time >= low,
            FgTraffic.time <= high,
            FgTraffic.srcmac.is_not(None),
            FgTraffic.srcintf.is_not(None),
            func.family(FgTraffic.srcip) == 4,
        )
        .group_by(FgTraffic.srcintf, FgTraffic.srcmac)
    )
    if interfaces is not None:
        query = query.where(FgTraffic.srcintf.in_(sorted(interfaces)))
    if macs is not None:
        query = query.where(FgTraffic.srcmac.in_(sorted(macs)))
    if untrusted:
        query = query.where(FgTraffic.source.not_in(sorted(untrusted)))
    return {
        (str(interface), str(mac)): int(count) for interface, mac, count in session.execute(query)
    }
