"""Network rules R01, R02, R08, R10, R19 (W6-B, contract §2.2.1, tests T-R, T-A7 and V6).

Every row is a raw line written to the spool as the collector writes it and parsed by the real
parsers (``arcilogger_ingest.parsers``), so the evidence of a hit must lead back to the line it
came from. The FortiGate and MikroTik lines follow the shapes of ``tests/fixtures/``; the DNS
response code field of R19 is an assumption (V6, sahada doğrula). Every value is fictional.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from ipaddress import ip_network
from typing import Any

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import RawRecord, format_line, parse_line
from arcilogger_core.timeutil import SITE_TZ, site_day, to_site
from arcilogger_db.models import Base, FgTraffic
from arcilogger_ingest.parsers import LinePosition, SourceType
from arcilogger_ingest.parsers import parse_line as parse_raw
from arcilogger_worker.rules import network
from arcilogger_worker.rules.model import Hit, RuleContext, RuleDef, RuleSettings

pytestmark = pytest.mark.usefixtures("db_session")

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
FIREWALL = "172.31.255.1"
ROUTER = "172.20.10.1"
SCANNER = "172.20.41.17"
OUTSIDE = "203.0.113.50"
INTERNAL = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
#: Values that would break a statement built from strings (T-A7).
HOSTILE = ("x'; DROP TABLE fg_traffic; --", "%", "_", "a\\b", '"', ";")


class Lines:
    """Raw lines in the spool and the typed rows the parser makes of them."""

    def __init__(self, session: Session, layout: Layout) -> None:
        self.session = session
        self.layout = layout
        self.count: dict[tuple[str, date], int] = {}

    def add(self, received: datetime, message: str, *, source: str, sender: str) -> int:
        day = site_day(received)
        path = self.layout.spool_file(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        offset = path.stat().st_size if path.exists() else 0
        data = format_line(received, sender, message.encode("utf-8"))
        with path.open("ab") as handle:
            handle.write(data)
        line = self.count.get((source, day), 0) + 1
        self.count[(source, day)] = line
        kind: SourceType = "fortigate" if source == "fortigate" else "mikrotik"
        outcome = parse_raw(
            RawRecord(received, sender, message.encode("utf-8")),
            LinePosition(source, kind, day, line, offset),
        )
        assert not outcome.failed, (outcome.error, outcome.detail, message)
        (row,) = outcome.rows
        table = Base.metadata.tables[row.table]
        return int(
            self.session.execute(
                insert(table).values(**row.values).returning(table.c.id)
            ).scalar_one()
        )

    def fortigate(self, received: datetime, kind: str, subtype: str, fields: str) -> int:
        """A FortiGate line (``format default``), device time 2 s before the receive time."""
        device = to_site(received - timedelta(seconds=2))
        stamp = int(device.timestamp()) * 1_000_000_000
        message = (
            f'<189>date={device:%Y-%m-%d} time={device:%H:%M:%S} devname="FIREWALL-01" '
            f'devid="FGT0000000000000" eventtime={stamp} tz="+0300" logid="0000000013" '
            f'type="{kind}" subtype="{subtype}" level="notice" vd="root" {fields}'
        )
        return self.add(received, message, source="fortigate", sender=FIREWALL)

    def traffic(
        self,
        received: datetime,
        srcip: str,
        dstip: str = "198.51.100.10",
        dstport: int = 443,
        *,
        action: str = "deny",
        policy: str = "WAN-IN",
    ) -> int:
        return self.fortigate(
            received,
            "traffic",
            "forward",
            f'srcip={srcip} srcport=51000 srcintf="port2" dstip={dstip} dstport={dstport} '
            f'dstintf="wan1" proto=6 action="{action}" policyid=12 policyname="{policy}" '
            f'service="TCP" sessionid={received.microsecond + 1}',
        )

    def web(self, received: datetime, srcip: str, catdesc: str) -> int:
        return self.fortigate(
            received,
            "utm",
            "webfilter",
            f'eventtype="ftgd_blk" srcip={srcip} srcport=51544 dstip=203.0.113.10 dstport=443 '
            f'hostname="gizli.example.com" action="blocked" url="/" cat=901 catdesc="{catdesc}"',
        )

    def dns(
        self, received: datetime, srcip: str, *, catdesc: str = "", rcode: str | None = None
    ) -> int:
        """A DNS filter response; ``rcode`` is the V6 assumption (sahada doğrula)."""
        code = f' rcode="{rcode}"' if rcode is not None else ""
        category = f' cat=901 catdesc="{catdesc}"' if catdesc else ""
        name = f"q{received.microsecond:06d}{received.second:02d}.example.net"
        return self.fortigate(
            received,
            "utm",
            "dns",
            f'eventtype="dns-response" srcip={srcip} srcport=53001 dstip=198.51.100.53 '
            f'dstport=53 proto=17 qname="{name}" qtype="A" action="pass"{category}{code}',
        )

    def app(self, received: datetime, srcip: str, app: str, appcat: str) -> int:
        return self.fortigate(
            received,
            "utm",
            "app-ctrl",
            f'eventtype="signature" appid=99101 srcip={srcip} dstip=203.0.113.75 '
            f'srcport=52814 dstport=443 proto=6 action="pass" appcat="{appcat}" app="{app}"',
        )

    def mikrotik_fw(self, received: datetime, src: str, dst: str, dport: int, prefix: str) -> int:
        stamp = to_site(received).isoformat(timespec="milliseconds")
        message = (
            f"<30>{stamp} CORE-R1 firewall,info {prefix} forward: in:vlan41 out:ether2, "
            f"connection-state:new src-mac 00:00:5E:00:53:17, proto TCP (SYN), "
            f"{src}:41822->{dst}:{dport}, len 60"
        )
        return self.add(received, message, source="core-r1", sender=ROUTER)


@pytest.fixture
def lines(db_session: Session, layout: Layout) -> Lines:
    return Lines(db_session, layout)


def context(
    rule: RuleDef, *, end: datetime = NOW, slice_seconds: int = 60, **params: object
) -> RuleContext:
    """The context the engine gives a rule for the slice ``(end - slice_seconds, end]``."""
    values = {p.key: p.default for p in rule.params}
    values.update(params)
    settings = RuleSettings(
        enabled=True,
        severity=rule.severity,
        window_seconds=rule.window_seconds,
        cooldown_seconds=rule.cooldown_seconds,
        params=values,
    )
    start = end - timedelta(seconds=slice_seconds)
    skew = timedelta(hours=1)
    return RuleContext(
        settings=settings,
        start=start,
        end=end,
        window_start=end - timedelta(seconds=rule.window_seconds),
        device_from=start - skew,
        device_to=end + skew,
        internal_networks=[ip_network(n) for n in INTERNAL],
        zone=SITE_TZ,
    )


def run(session: Session, rule: RuleDef, **kwargs: Any) -> list[Hit]:
    return list(rule.evaluate(session, context(rule, **kwargs)))


def spread(count: int, seconds: float, *, end: datetime = NOW) -> list[datetime]:
    """``count`` receive times evenly inside ``(end - seconds, end]``, oldest first."""
    step = seconds / count
    return [end - timedelta(seconds=seconds - step * (n + 1)) for n in range(count)]


def assert_opens(layout: Layout, hit: Hit) -> None:
    """Every evidence reference names its own spool line (what ``GET /api/v1/raw`` opens)."""
    for ref in hit.evidence:
        data = layout.spool_file(ref.source, ref.raw_day).read_bytes()
        assert data[: ref.raw_offset].count(b"\n") == ref.raw_line - 1
        line = data[ref.raw_offset :].split(b"\n", 1)[0]
        record = parse_line(line)
        assert record.received == ref.time_received
        assert hit.key.encode() in record.message


def only(hits: Sequence[Hit]) -> Hit:
    assert len(hits) == 1, [h.key for h in hits]
    return hits[0]


# -- R01 ------------------------------------------------------------------------------------------


def test_r01_internal_port_scan(db_session: Session, lines: Lines, layout: Layout) -> None:
    for n, at in enumerate(spread(30, 59)):
        lines.traffic(at, SCANNER, dstport=1000 + n)
    lines.traffic(NOW, "172.20.41.18", dstport=22)  # another address, one port
    lines.traffic(NOW, SCANNER, dstport=5000, action="accept")  # not blocked
    hit = only(run(db_session, network.R01))
    assert (hit.key, hit.count, hit.time) == (SCANNER, 30, NOW)
    assert hit.values == {"count": 30, "ports": 30, "hosts": 1, "window_seconds": 60}
    assert len(hit.evidence) == 20  # rules.evidence_max, the oldest first
    assert [e.time_received for e in hit.evidence] == spread(30, 59)[:20]
    assert {e.tbl for e in hit.evidence} == {"fg_traffic"}
    assert_opens(layout, hit)


def test_r01_one_port_below_the_threshold(db_session: Session, lines: Lines) -> None:
    for n, at in enumerate(spread(29, 59)):
        lines.traffic(at, SCANNER, dstport=1000 + n)
        lines.traffic(at, SCANNER, dstport=1000 + n)  # repeats do not count
    lines.traffic(NOW - timedelta(seconds=61), SCANNER, dstport=999)  # before the window
    assert run(db_session, network.R01) == []


def test_r01_hosts(db_session: Session, lines: Lines) -> None:
    for n, at in enumerate(spread(50, 59)):
        lines.traffic(at, SCANNER, dstip=f"172.20.50.{n + 1}", dstport=445)
    hit = only(run(db_session, network.R01))
    assert (hit.values["hosts"], hit.values["ports"]) == (50, 1)
    for n, at in enumerate(spread(49, 59, end=NOW + timedelta(hours=1))):
        lines.traffic(at, "172.20.41.30", dstip=f"172.20.51.{n + 1}", dstport=445)
    assert run(db_session, network.R01, end=NOW + timedelta(hours=1)) == []


def test_r01_external_sources_and_old_slices_do_not_count(
    db_session: Session, lines: Lines
) -> None:
    for n, at in enumerate(spread(40, 59)):
        lines.traffic(at, OUTSIDE, dstport=1000 + n)
    assert run(db_session, network.R01) == []
    # every row of the scan before this slice: the address is not evaluated again
    for n, at in enumerate(spread(40, 50, end=NOW - timedelta(seconds=10))):
        lines.traffic(at, SCANNER, dstport=1000 + n)
    assert run(db_session, network.R01, slice_seconds=5) == []
    assert only(run(db_session, network.R01, slice_seconds=15)).key == SCANNER


def test_r01_mikrotik_lines_count_when_their_prefix_is_listed(
    db_session: Session, lines: Lines, layout: Layout
) -> None:
    for n, at in enumerate(spread(30, 59)):
        if n % 2:
            lines.traffic(at, SCANNER, dstport=2000 + n)
        else:
            lines.mikrotik_fw(at, SCANNER, "172.20.20.44", 2000 + n, "DROP-FWD")
    assert run(db_session, network.R01) == []  # default: MikroTik not read
    hit = only(run(db_session, network.R01, mikrotik_prefixes=["DROP-FWD"]))
    assert (hit.count, hit.values["ports"], hit.values["hosts"]) == (30, 30, 2)
    assert {e.tbl for e in hit.evidence} == {"fg_traffic", "mt_fw"}
    assert_opens(layout, hit)
    assert run(db_session, network.R01, mikrotik_prefixes=["DROP-WAN"]) == []


# -- R02 ------------------------------------------------------------------------------------------


def test_r02_external_scan(db_session: Session, lines: Lines, layout: Layout) -> None:
    for at in spread(100, 299):
        lines.traffic(at, OUTSIDE, dstip="198.51.100.1", dstport=3389)
    lines.traffic(NOW, SCANNER)  # internal: R01's business
    hit = only(run(db_session, network.R02))
    assert (hit.key, hit.count, hit.values) == (
        OUTSIDE,
        100,
        {"count": 100, "window_seconds": 300},
    )
    assert_opens(layout, hit)
    assert run(db_session, network.R02, policy_names=["VPN-IN"]) == []
    assert only(run(db_session, network.R02, policy_names=["VPN-IN", "WAN-IN"])).count == 100


def test_r02_below_the_threshold(db_session: Session, lines: Lines) -> None:
    for at in spread(99, 299):
        lines.traffic(at, OUTSIDE, dstport=3389)
    lines.traffic(NOW, OUTSIDE, dstport=3389, action="accept")
    lines.traffic(NOW - timedelta(seconds=301), OUTSIDE, dstport=3389)
    assert run(db_session, network.R02) == []


# -- R08 ------------------------------------------------------------------------------------------


def test_r08_needs_setup_until_dns_servers_are_listed() -> None:
    assert network.R08.needs_setup is not None
    defaults = context(network.R08).settings
    assert network.R08.needs_setup(defaults)
    allowed = replace(defaults, params={**defaults.params, "allowed_dns_servers": ["172.20.10.1"]})
    assert not network.R08.needs_setup(allowed)


def test_r08_dns_bypass(db_session: Session, lines: Lines, layout: Layout) -> None:
    for n, at in enumerate(spread(20, 3599)):
        server = "198.51.100.53" if n % 2 else "203.0.113.53"
        lines.traffic(at, SCANNER, dstip=server, dstport=853 if n == 0 else 53, action="accept")
    for at in spread(30, 3599):
        lines.traffic(at, SCANNER, dstip="172.20.10.1", dstport=53, action="accept")
    hit = only(run(db_session, network.R08, allowed_dns_servers=["172.20.10.0/30"]))
    assert (hit.key, hit.count, hit.values["servers"]) == (SCANNER, 20, 2)
    assert_opens(layout, hit)


def test_r08_below_the_threshold(db_session: Session, lines: Lines) -> None:
    for at in spread(19, 3599):
        lines.traffic(at, SCANNER, dstip="198.51.100.53", dstport=53, action="accept")
    lines.traffic(NOW, SCANNER, dstip="198.51.100.53", dstport=443, action="accept")
    lines.traffic(NOW, OUTSIDE, dstip="198.51.100.53", dstport=53, action="accept")
    assert run(db_session, network.R08, allowed_dns_servers=["172.20.10.1"]) == []


# -- R10 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["web", "dns", "app", "appcat"])
def test_r10_proxy_vpn_tor(db_session: Session, lines: Lines, layout: Layout, kind: str) -> None:
    at = NOW - timedelta(seconds=30)
    if kind == "web":
        lines.web(at, SCANNER, "Proxy Avoidance")
    elif kind == "dns":
        lines.dns(at, SCANNER, catdesc="Proxy Avoidance")
    elif kind == "app":
        lines.app(at, SCANNER, "Tor", "Network.Service")
    else:
        lines.app(at, SCANNER, "Example.Tunnel", "Proxy")
    hit = only(run(db_session, network.R10))
    assert (hit.key, hit.count, hit.values) == (SCANNER, 1, {"count": 1, "window_seconds": 3600})
    assert_opens(layout, hit)


def test_r10_nothing_listed_matches(db_session: Session, lines: Lines) -> None:
    at = NOW - timedelta(seconds=30)
    lines.web(at, SCANNER, "Synthetic Category A")
    lines.dns(at, SCANNER, catdesc="Synthetic Category B")
    lines.app(at, SCANNER, "Example.Docs", "General.Interest")
    lines.web(NOW - timedelta(seconds=3601), SCANNER, "Proxy Avoidance")  # before the window
    assert run(db_session, network.R10) == []
    lines.web(at, SCANNER, "Proxy Avoidance")
    assert run(db_session, network.R10, categories=[], apps=[], appcats=[]) == []


# -- R19 ------------------------------------------------------------------------------------------


def test_r19_suspicious_dns(db_session: Session, lines: Lines, layout: Layout) -> None:
    for n, at in enumerate(spread(201, 3599)):
        lines.dns(at, SCANNER, rcode="3" if n == 0 else "NXDOMAIN")
    lines.dns(NOW, "172.20.41.18", rcode="NOERROR")
    hit = only(run(db_session, network.R19))
    assert (hit.key, hit.count, hit.values["names"]) == (SCANNER, 201, 201)
    assert_opens(layout, hit)


def test_r19_at_the_threshold_is_not_more(db_session: Session, lines: Lines) -> None:
    for at in spread(200, 3599):
        lines.dns(at, SCANNER, rcode="NXDOMAIN")
    lines.dns(NOW, SCANNER, rcode="NOERROR")
    lines.dns(NOW, SCANNER)
    assert run(db_session, network.R19) == []


def test_r19_formats(db_session: Session, lines: Lines) -> None:
    """V6 (sahada doğrula): the FortiGate parser keeps a DNS log's response code in
    ``extra["rcode"]`` as sent, and R19 matches the row it makes."""
    row_id = lines.dns(NOW, SCANNER, rcode="NXDOMAIN")
    extra = db_session.execute(select(Base.metadata.tables["fg_dns"].c.extra)).scalar_one()
    assert extra.get("rcode") == "NXDOMAIN", (
        f"V6: the parser no longer keeps the DNS response code in extra['rcode']: {extra}"
    )
    hit = only(run(db_session, network.R19, threshold=0))
    assert [e.row_id for e in hit.evidence] == [row_id], "V6: R19 does not match the parsed row"
    assert network.R19.format_verified is False


# -- T-A7: bound parameters -----------------------------------------------------------------------


def test_list_parameters_are_bound(db_session: Session, lines: Lines) -> None:
    """Quotes, semicolons and ``%`` in every list parameter change nothing but the match."""
    for n, at in enumerate(spread(30, 59)):
        lines.traffic(at, SCANNER, dstport=1000 + n)
        lines.mikrotik_fw(at, "172.20.41.40", "172.20.20.44", 1000 + n, "DROP-FWD")
    for at in spread(5, 59):
        lines.traffic(at, OUTSIDE)
        lines.traffic(at, "172.20.41.50", dstip="198.51.100.53", dstport=53, action="accept")
    lines.web(NOW, SCANNER, "Proxy Avoidance")
    for at in spread(201, 59):
        lines.dns(at, SCANNER, rcode="NXDOMAIN")
    traffic = db_session.execute(select(func.count()).select_from(FgTraffic)).scalar_one()
    hostile = list(HOSTILE)
    cases: list[tuple[RuleDef, dict[str, object], list[str]]] = [
        (network.R01, {"deny_actions": hostile}, []),
        (network.R01, {"deny_actions": [*hostile, "deny"]}, [SCANNER]),
        (network.R01, {"mikrotik_prefixes": hostile}, [SCANNER]),
        (network.R01, {"mikrotik_prefixes": [*hostile, "DROP-FWD"]}, [SCANNER, "172.20.41.40"]),
        (network.R02, {"threshold": 1, "policy_names": hostile}, []),
        (network.R02, {"threshold": 1, "policy_names": [*hostile, "WAN-IN"]}, [OUTSIDE]),
        (network.R02, {"threshold": 1, "deny_actions": hostile}, []),
        (network.R08, {"threshold": 1, "allowed_dns_servers": ["172.20.10.1"]}, ["172.20.41.50"]),
        (network.R10, {"categories": hostile, "apps": hostile, "appcats": hostile}, []),
        (network.R10, {"categories": [*hostile, "Proxy Avoidance"]}, [SCANNER]),
        (network.R19, {"nxdomain_values": hostile}, []),
        (network.R19, {"nxdomain_values": [*hostile, "NXDOMAIN"]}, [SCANNER]),
        (network.R19, {"rcode_field": ["x'; --"]}, []),
    ]
    for rule, params, keys in cases:
        assert sorted(h.key for h in run(db_session, rule, **params)) == keys, (rule.id, params)
    assert db_session.execute(select(func.count()).select_from(FgTraffic)).scalar_one() == traffic
