"""``alerts.system``: the lease evidence and internet exit checks of ADR-0016 (``R20-lease:*``,
``R20-nat:*``) — every cause with its severity, the details 2.16 allows (no client MAC, no
client IP but the declined pool addresses), its resolution, and the e-mail line that names the
rule, the cause's fixed action and its counts only."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_db.engine import session_factory
from arcilogger_db.models import (
    Alert,
    FgEvent,
    FgTraffic,
    JobRun,
    Lease,
    Message,
    ParseFailure,
    SearchCount,
    Setting,
    Source,
    Vlan,
)
from arcilogger_db.settings import MIGRATION_AUTHOR, SEED_AUTHOR, set_setting
from arcilogger_worker.alerts import system
from arcilogger_worker.alerts.mail import MailItem, render_text
from arcilogger_worker.alerts.system import masked, run_system_checks
from arcilogger_worker.alerts.texts import (
    LEASE_CAUSES,
    LEASE_TEXTS,
    NAT_CAUSES,
    RULES,
    describe,
    runbook,
)
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 20)
NOW = tr(DAY, 12)
HOUR = timedelta(hours=1)
GIB = 1024**3


class Usage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.fixture(autouse=True)
def only_these_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two ADR-0016 checks, as alerts.system runs them (own transactions)."""
    checks = tuple(
        (name, check) for name, check in system.CHECKS if name in ("lease_evidence", "nat_exits")
    )
    assert len(checks) == 2
    monkeypatch.setattr(system, "CHECKS", checks)


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


@pytest.fixture
def config(layout: Layout) -> Config:
    return Config(data_dir=layout.root, secrets_dir=layout.root)


@pytest.fixture
def check(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> Iterator[Any]:
    def run(now: datetime = NOW) -> dict[str, Alert]:
        db_session.commit()
        run_system_checks(sessions, layout, config, now=now)
        return active(db_session)

    yield run


def active(session: Session) -> dict[str, Alert]:
    session.expire_all()
    rows = session.scalars(select(Alert).where(Alert.state.in_(("open", "acknowledged"))))
    return {row.dedup_key or "": row for row in rows}


def resolved(session: Session) -> dict[str, str]:
    session.expire_all()
    rows = session.scalars(select(Alert).where(Alert.state == "resolved"))
    return {row.dedup_key or "": row.details["auto_resolved"]["reason"] for row in rows}


def office_traffic(evidence: Evidence, now: datetime = NOW, ip: str = "172.20.43.20") -> None:
    """A session of an internal client in the last hour (the lease checks' precondition)."""
    evidence.traffic(now - timedelta(minutes=10), ip, 50000)


# -- none / absent --------------------------------------------------------------------------------


def test_fortigate_dhcp_evidence_that_stopped(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(NOW - timedelta(days=5), "172.20.42.57", "00:00:5e:00:53:57")
    office_traffic(evidence)
    alerts = check()
    alert = alerts["R20-lease:none:fg_dhcp"]
    assert (alert.rule, alert.severity) == ("R20-lease", "high")
    assert alert.title == "Kira kanıtı kesildi: FortiGate DHCP kayıtları gelmiyor"
    assert alert.details["table"] == "fg_dhcp"
    assert alert.details["title_en"] == "Lease evidence stopped: no FortiGate DHCP records"
    assert "Son 24 saatte kullanılabilir hiçbir FortiGate DHCP kaydı" in alert.details["action_tr"]
    assert "R20-lease:absent" not in alerts
    # A usable ACK arrives: resolved at once.
    evidence.fortigate_dhcp(NOW - timedelta(minutes=5), "172.20.42.57", "00:00:5e:00:53:57")
    assert "R20-lease:none:fg_dhcp" not in check()
    assert resolved(db_session)["R20-lease:none:fg_dhcp"] == "evidence_arrived"


def test_a_week_lease_needs_no_ack_for_three_days(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    """Open FortiGate leases of 604,800 s: a working feed shows an ACK within half of it plus
    24 hours (4.5 days)."""
    evidence.fortigate_dhcp(NOW - timedelta(days=9), "172.20.42.57", "00:00:5e:00:53:57")
    evidence.fortigate_dhcp(NOW - timedelta(days=3), "172.20.42.57", "00:00:5e:00:53:57",
                            lease=604800)  # fmt: skip
    db_session.add(
        Lease(ip="172.20.42.57", mac="00:00:5e:00:53:57", start=NOW - timedelta(days=9),
              confidence="high", source_table="fg_dhcp", lease_seconds=604800)
    )  # fmt: skip
    office_traffic(evidence)
    assert "R20-lease:none:fg_dhcp" not in check()
    db_session.execute(update(Lease).values(end=NOW - HOUR, end_reason="timeout"))
    assert "R20-lease:none:fg_dhcp" in check()  # no open lease: the 24-hour window


def test_a_mixed_site_whose_fortigate_dhcp_feed_stops(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    """MikroTik DHCP for VLAN 41 keeps arriving; FortiGate DHCP for VLAN 60 stopped."""
    evidence.fortigate_dhcp(NOW - timedelta(days=2), "172.20.60.20", "02:00:5e:00:53:20",
                            interface="vlan60")  # fmt: skip
    evidence.dhcp(NOW - HOUR, "assigned", "172.20.41.10", "00:00:5e:00:53:10", server="dhcp41")
    evidence.dhcp(NOW - timedelta(days=3), "assigned", "172.20.41.11", "00:00:5e:00:53:11",
                  server="dhcp41")  # fmt: skip
    office_traffic(evidence)
    alerts = check()
    assert alerts["R20-lease:none:fg_dhcp"].severity == "high"
    assert "R20-lease:none:mt_dhcp" not in alerts


def test_an_arp_log_script_that_stops(check: Any, evidence: Evidence) -> None:
    evidence.arp(NOW - timedelta(hours=30), "172.20.10.237", "00:1b:a9:00:00:10", "vlan10")
    evidence.dhcp(NOW - HOUR, "assigned", "172.20.43.120", "00:00:5e:00:53:20")
    office_traffic(evidence)
    alerts = check()
    alert = alerts["R20-lease:none:mt_arp"]
    assert (alert.severity, alert.details["table"]) == ("high", "mt_arp")
    assert "R20-lease:none:mt_dhcp" not in alerts


@pytest.mark.parametrize("arp_log", [False, True])
def test_a_mikrotik_dhcp_topic_that_stops(check: Any, evidence: Evidence, arp_log: bool) -> None:
    """Medium, and only while no ARP-LOG row arrives either (RouterOS logs no renewals)."""
    evidence.dhcp(NOW - timedelta(days=2), "assigned", "172.20.43.120", "00:00:5e:00:53:20")
    if arp_log:
        evidence.arp(NOW - HOUR, "172.20.43.120", "00:00:5e:00:53:20")
    office_traffic(evidence, ip="172.20.20.10")  # servers keep sending traffic
    alerts = check()
    if arp_log:
        assert "R20-lease:none:mt_dhcp" not in alerts
    else:
        assert alerts["R20-lease:none:mt_dhcp"].severity == "medium"


def test_no_lease_evidence_at_all(check: Any, db_session: Session, evidence: Evidence) -> None:
    office_traffic(evidence)
    alerts = check()
    alert = alerts["R20-lease:absent"]
    assert alert.severity == "info"
    assert [key for key in alerts if key.startswith("R20-lease:none")] == []
    # Without traffic from the internal network nothing is judged.
    db_session.execute(update(Alert).values(state="dismissed"))
    db_session.execute(update(FgTraffic).values(srcip="203.0.113.7"))
    assert "R20-lease:absent" not in check()


# -- refused rows ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        {"dhcp_msg": "DHCPACK"},
        {"device_time": False},
        {"source": "unknown-192.0.2.7"},
    ],
)
def test_every_row_refused_is_high(
    check: Any, db_session: Session, evidence: Evidence, case: dict[str, Any]
) -> None:
    db_session.add(Source(name="unknown-192.0.2.7", type="fortigate", address="192.0.2.7",
                          unknown=True))  # fmt: skip
    for n in range(12):
        evidence.fortigate_dhcp(NOW - timedelta(minutes=30 + n), f"172.20.42.{60 + n}",
                                f"00:00:5e:00:53:{60 + n:02x}", **case)  # fmt: skip
    alert = check()["R20-lease:fortigate-refused"]
    assert alert.severity == "high"
    assert alert.title == "FortiGate DHCP kayıtları geliyor ama kullanılmıyor"
    assert (alert.details["count"], alert.details["rows"], alert.details["kept"]) == (12, 12, 0)
    reason = {"dhcp_msg": "unexpected_msg", "device_time": "receive_time"}.get(
        next(iter(case)), "untrusted"
    )
    assert alert.details["reasons"] == {reason: 12}
    if reason == "untrusted":
        assert alert.details["sources"] == ["unknown-192.0.2.7"]
    if reason == "unexpected_msg":
        assert alert.details["dhcp_msgs"] == ["DHCPACK"]
    assert alert.details["first_day"] == DAY.isoformat()


def test_one_refused_row_among_usable_ones_is_medium(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    db_session.add(Source(name="unknown-192.0.2.7", type="fortigate", address="192.0.2.7",
                          unknown=True))  # fmt: skip
    at = NOW - HOUR
    for n in range(5):
        evidence.fortigate_dhcp(at, f"172.20.42.{10 + n}", f"00:00:5e:00:53:{10 + n:02x}")
    long_value = "Ack-" + "x" * 36  # 40 characters
    evidence.fortigate_dhcp(at, "172.20.42.20", "00:00:5e:00:53:20", dhcp_msg=long_value)
    evidence.fortigate_dhcp(at, "172.20.42.21", "00:00:5e:00:53:21",
                            dhcp_msg="Ack 00:00:5e:00:53:99 to 172.20.42.99")  # fmt: skip
    evidence.fortigate_dhcp(at, "172.20.42.22", "00:00:5e:00:53:22", logid="0100026099")
    # An untrusted sender's dhcp_msg is never shown: counted as untrusted first.
    evidence.fortigate_dhcp(at, "172.20.42.23", "00:00:5e:00:53:23", dhcp_msg="Sahte",
                            source="unknown-192.0.2.7")  # fmt: skip
    alert = check()["R20-lease:fortigate-refused"]
    assert alert.severity == "medium"
    assert alert.title == "FortiGate DHCP kayıtlarının bir kısmı kullanılmıyor"
    details = alert.details
    assert details["reasons"] == {"unexpected_msg": 2, "unmapped": 1, "untrusted": 1}
    assert (details["count"], details["rows"], details["kept"]) == (4, 9, 5)
    assert details["dhcp_msgs"] == sorted(["Ack … to …", ("Ack-" + "x" * 36)[:32]])
    assert details["logids"] == ["0100026099"]
    assert details["sources"] == ["unknown-192.0.2.7"]
    assert "Sahte" not in str(details)
    assert "beklenmeyen dhcp_msg 2 (Ack … to …, Ack-xxxx" in details["action_tr"]
    assert "unexpected dhcp_msg 2 (" in details["action_en"]


def test_excluded_interfaces_and_ipv6_are_no_refusal(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    """DEVICE-SETUP 2.4 step 2: DHCP over IPsec excluded gives 90 % of the rows, no alert; the
    outside-network and lease-time causes ignore them too."""
    set_setting(db_session, "dhcp.fortigate_interfaces_excluded", ["ipsec1"], updated_by="tolga")
    at = NOW - HOUR
    for n in range(9):
        evidence.fortigate_dhcp(at, f"10.99.0.{n + 1}", f"00:00:5e:00:53:{n + 1:02x}",
                                interface="ipsec1", lease=10**7)  # fmt: skip
    evidence.fortigate_dhcp(at, "172.20.42.57", "00:00:5e:00:53:57")
    evidence.fortigate_dhcp(at, "2001:db8::57", "00:00:5e:00:53:58")
    alerts = check()
    for cause in ("fortigate-refused", "fortigate-dhcp-subnet", "fortigate-lease"):
        assert f"R20-lease:{cause}" not in alerts


def test_refused_rows_with_fortigate_evidence_switched_off(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.57", "00:00:5e:00:53:57", dhcp_msg="Nak")
    assert "R20-lease:fortigate-refused" in check()
    set_setting(db_session, "dhcp.fortigate_logids", {}, updated_by="tolga")
    assert "R20-lease:fortigate-refused" not in check()
    assert resolved(db_session)["R20-lease:fortigate-refused"] == "fortigate_dhcp_off"


def test_a_corrected_setting_resolves_at_once_receive_time_rows_after_a_day(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(NOW - timedelta(days=3), "172.20.42.50", "00:00:5e:00:53:50",
                            dhcp_msg="DHCPACK")  # fmt: skip
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.57", "00:00:5e:00:53:57", dhcp_msg="DHCPACK")
    alert = check()["R20-lease:fortigate-refused"]
    # The oldest day with a refused row within 100 days: rebuild from there.
    assert alert.details["first_day"] == (DAY - timedelta(days=3)).isoformat()
    assert "17.10.2026 gününden bugüne" in alert.details["action_tr"]
    set_setting(db_session, "dhcp.fortigate_dhcp_msgs", {"0100026001": ["Ack", "DHCPACK"]},
                updated_by="tolga")  # fmt: skip
    assert "R20-lease:fortigate-refused" not in check()
    assert resolved(db_session)["R20-lease:fortigate-refused"] == "no_refused_rows"
    # Receive-time rows stay refused until they leave the 24-hour window.
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.58", "00:00:5e:00:53:58", device_time=False)
    assert "R20-lease:fortigate-refused" in check()
    assert "R20-lease:fortigate-refused" in check(NOW + timedelta(hours=22))
    assert "R20-lease:fortigate-refused" not in check(NOW + timedelta(hours=24))


def test_a_sender_configured_later_resolves_its_refusal(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    db_session.add(Source(name="unknown-172.31.255.2", type="fortigate", address="172.31.255.2",
                          unknown=True))  # fmt: skip
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.57", "00:00:5e:00:53:57",
                            source="unknown-172.31.255.2")  # fmt: skip
    assert "R20-lease:fortigate-refused" in check()
    db_session.add(Source(name="fortigate-ha", type="fortigate", address="172.31.255.0/29"))
    assert "R20-lease:fortigate-refused" not in check()


def test_every_row_refused_and_the_feed_stopped(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(NOW - timedelta(days=4), "172.20.42.50", "00:00:5e:00:53:50")
    for n in range(3):
        evidence.fortigate_dhcp(NOW - HOUR, f"172.20.42.{60 + n}", f"00:00:5e:00:53:{60 + n:02x}",
                                dhcp_msg="DHCPACK")  # fmt: skip
    office_traffic(evidence)
    alerts = check()
    assert {"R20-lease:fortigate-refused", "R20-lease:none:fg_dhcp"} <= set(alerts)


# -- unconverted, unverified, lease values, outside networks --------------------------------------


def _fg_event(session: Session, extra: dict[str, Any], *, logid: str = "0100026001") -> None:
    session.add(
        FgEvent(time=NOW - HOUR, time_received=NOW - HOUR, source="fortigate",
                sender="172.31.255.1", raw_day=DAY, raw_line=1, raw_offset=0, logid=logid,
                subtype="system", extra=extra)
    )  # fmt: skip


def test_dhcp_lines_that_kept_the_event_shape(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    # A line loaded before the upgrade has all three keys in extra: not a format problem.
    _fg_event(db_session, {"ip": "172.20.42.57", "mac": "00:00:5E:00:53:57", "dhcp_msg": "Ack"})
    _fg_event(db_session, {"ip": "172.20.42.58"}, logid="0100026003")  # not a mapped logid
    assert "R20-lease:fortigate-unconverted" not in check()
    _fg_event(db_session, {"ip": "172.20.42.59", "dhcp_msg": "Ack"})  # no mac: other format
    db_session.add(
        ParseFailure(source="fortigate", day=DAY, pattern="fortigate.dhcp", count=3,
                     first_seen=NOW - HOUR, last_seen=NOW - HOUR, example_line=1,
                     example_offset=0)
    )  # fmt: skip
    alert = check()["R20-lease:fortigate-unconverted"]
    assert alert.severity == "high"
    assert (alert.details["rows"], alert.details["failures"], alert.details["count"]) == (1, 3, 4)
    assert "Son 24 saatte 1 kayıt DHCP logid'iyle geldi" in alert.details["action_tr"]
    assert "(3 parse failures)" in alert.details["action_en"]


def test_the_unverified_format_until_the_setting_is_on(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(NOW - timedelta(days=2), "172.20.42.57", "00:00:5e:00:53:57")
    alert = check()["R20-lease:fortigate-unverified"]
    assert alert.severity == "info"
    # Still open a day later (only the setting resolves it).
    assert "R20-lease:fortigate-unverified" in check(NOW + timedelta(days=2))
    set_setting(db_session, "dhcp.fortigate_format_verified", True, updated_by="tolga")
    assert "R20-lease:fortigate-unverified" not in check(NOW + timedelta(days=2))
    assert resolved(db_session)["R20-lease:fortigate-unverified"] == "format_verified"


def test_unlimited_and_unexpected_lease_times(check: Any, evidence: Evidence) -> None:
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.57", "00:00:5e:00:53:57", lease=0)
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.58", "00:00:5e:00:53:58", lease=10**7)
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.59", "00:00:5e:00:53:59", lease=86400)
    alerts = check()
    unlimited = alerts["R20-lease:fortigate-lease-unlimited"]
    assert (unlimited.severity, unlimited.details["count"]) == ("high", 1)
    odd = alerts["R20-lease:fortigate-lease"]
    assert (odd.severity, odd.details["count"]) == ("low", 1)


def test_rows_outside_every_network_name_the_day_to_rebuild_from(
    check: Any, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(NOW - timedelta(days=6), "10.99.0.5", "00:00:5e:00:53:05",
                            interface="ssl.root")  # fmt: skip
    evidence.fortigate_dhcp(NOW - HOUR, "10.99.0.6", "00:00:5e:00:53:06", interface="ssl.root")
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.57", "00:00:5e:00:53:57")
    alert = check()["R20-lease:fortigate-dhcp-subnet"]
    assert alert.severity == "low"
    details = alert.details
    assert (details["count"], details["interfaces"]) == (1, ["ssl.root"])
    assert details["first_day"] == (DAY - timedelta(days=6)).isoformat()
    assert "14.10.2026 gününden bugüne" in details["action_tr"]
    assert "from 14.10.2026 to today" in details["action_en"]
    assert "Devices and people → Departments and VLANs → Add a VLAN" in details["action_en"]
    assert "Rebuild the leases" in details["action_en"]


# -- networks: decline, two DHCP sources, one MAC on many addresses -------------------------------


def _rebuild(session: Session, now: datetime = NOW) -> None:
    session.commit()
    rebuild_day(session, DAY, horizon=now)
    session.commit()


def test_a_static_device_in_the_pool(check: Any, db_session: Session, evidence: Evidence) -> None:
    """D33: two clients each leave .57 within seconds of its ACK for another address."""
    for minute, mac, other in ((0, "00:00:5e:00:53:31", "58"), (30, "00:00:5e:00:53:32", "59")):
        at = tr(DAY, 8, minute)
        evidence.fortigate_dhcp(at, "172.20.42.57", mac)
        evidence.fortigate_dhcp(at + timedelta(seconds=4), f"172.20.42.{other}", mac)
    _rebuild(db_session)
    alert = check()["R20-lease:fortigate-decline:42"]
    assert alert.severity == "high"
    assert alert.title == "VLAN 42 DHCP havuzunda statik adresli bir cihaz olabilir"
    assert (alert.details["vlan"], alert.details["count"]) == (42, 2)
    assert alert.details["addresses"] == ["172.20.42.57"]  # the Security page only
    mail = render_text([_item(alert)], "https://arcilogger.example.com")
    assert "172.20.42.57" not in mail
    assert "VLAN 42" in mail


def test_a_single_move_or_a_move_to_another_network_is_no_decline(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.42.57", "00:00:5e:00:53:31")
    evidence.fortigate_dhcp(tr(DAY, 8, 0, 4), "172.20.42.58", "00:00:5e:00:53:31")
    # A laptop that gets an address on its second VLAN moves nothing.
    evidence.fortigate_dhcp(tr(DAY, 9), "172.20.42.57", "00:00:5e:00:53:32")
    evidence.fortigate_dhcp(tr(DAY, 9, 0, 4), "172.20.43.58", "00:00:5e:00:53:32",
                            interface="vlan43")  # fmt: skip
    _rebuild(db_session)
    assert [key for key in check() if "decline" in key] == []


def test_a_decline_on_a_network_without_a_vlan_number(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    db_session.add(Vlan(subnet="172.20.70.0/24", vlan_id=None, name_tr="Ofis"))
    for minute, mac, other in ((0, "00:00:5e:00:53:31", "58"), (30, "00:00:5e:00:53:32", "59")):
        at = tr(DAY, 8, minute)
        evidence.fortigate_dhcp(at, "172.20.70.57", mac, interface="internal")
        evidence.fortigate_dhcp(at + timedelta(seconds=4), f"172.20.70.{other}", mac,
                                interface="internal")  # fmt: skip
    _rebuild(db_session)
    alert = check()["R20-lease:fortigate-decline:172.20.70.0_24"]
    assert alert.title == "Ofis (172.20.70.0/24) DHCP havuzunda statik adresli bir cihaz olabilir"
    assert "vlan" not in alert.details
    mail = render_text([_item(alert)], "https://arcilogger.example.com")
    for hidden in ("172.20.70", "Ofis", "VLAN"):
        assert hidden not in mail


def test_two_dhcp_sources_on_one_network(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.dhcp(NOW - HOUR, "assigned", "172.20.41.10", "00:00:5e:00:53:10", server="dhcp41")
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.41.11", "00:00:5e:00:53:11", interface="vlan41")
    long_vd = "vd-" + "v" * 37  # 40 characters
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.43.11", "00:00:5e:00:53:12", interface="vlan43")
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.43.12", "00:00:5e:00:53:13", interface="vlan43",
                            vd=long_vd)  # fmt: skip
    # One HA pair: two senders, one VDOM (and devid is not compared): one origin.
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.60.11", "00:00:5e:00:53:14", interface="vlan60")
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.60.12", "00:00:5e:00:53:15", interface="vlan60",
                            source="fortigate-ha2")  # fmt: skip
    alerts = check()
    mixed = alerts["R20-lease:dhcp-sources-overlap:41"]
    assert mixed.severity == "high"
    assert mixed.details["origins"] == ["FortiGate DHCP (VDOM root)", "MikroTik DHCP"]
    assert mixed.title == "Aynı ağda iki DHCP kaynağı: VLAN 41"
    vdoms = alerts["R20-lease:dhcp-sources-overlap:43"]
    assert vdoms.details["origins"] == [
        "FortiGate DHCP (VDOM root)",
        f"FortiGate DHCP (VDOM {long_vd[:32]})",
    ]
    assert "R20-lease:dhcp-sources-overlap:60" not in alerts


def test_one_mac_on_many_addresses_of_one_network(check: Any, evidence: Evidence) -> None:
    for n in range(4):
        evidence.fortigate_dhcp(NOW - HOUR + timedelta(minutes=n), f"172.20.42.{70 + n}",
                                "00:00:5e:00:53:70")  # fmt: skip
    alert = check()["R20-lease:fortigate-dhcp-mac"]
    assert (alert.severity, alert.details["count"]) == ("medium", 1)
    assert "00:00:5e:00:53:70" not in str(alert.details)


def test_a_laptop_on_two_vlans_is_no_shared_mac(check: Any, evidence: Evidence) -> None:
    for n in range(2):
        evidence.fortigate_dhcp(NOW - HOUR, f"172.20.42.{70 + n}", "00:00:5e:00:53:70")
        evidence.fortigate_dhcp(NOW - HOUR, f"172.20.43.{70 + n}", "00:00:5e:00:53:70",
                                interface="vlan43")  # fmt: skip
    assert "R20-lease:fortigate-dhcp-mac" not in check()


def _item(alert: Alert) -> MailItem:
    return MailItem(
        alert.id, "new", alert.rule, alert.severity, alert.state, alert.time, None, alert.details
    )
