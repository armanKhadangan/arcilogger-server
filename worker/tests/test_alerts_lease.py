"""``alerts.system``: the lease evidence and internet exit checks of ADR-0016 (``R20-lease:*``,
``R20-nat:*``) — every cause with its severity, the details 2.16 allows (no client MAC, no
client IP but the declined pool addresses), its resolution, and the e-mail line that names the
rule, the cause's fixed action and its counts only."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from string import Formatter
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
from arcilogger_worker.alerts.system import run_system_checks
from arcilogger_worker.alerts.texts import (
    LEASE_CAUSES,
    LEASE_TEXTS,
    NAT_CAUSES,
    NAT_TEXTS,
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
) -> Any:
    def run(now: datetime = NOW) -> dict[str, Alert]:
        db_session.commit()
        run_system_checks(sessions, layout, config, now=now)
        return active(db_session)

    return run


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


# -- source clocks --------------------------------------------------------------------------------


def _clock(
    session: Session,
    name: str,
    offset: timedelta,
    *,
    kind: str = "fortigate",
    table: str = "fg_traffic",
    rows: int = 20,
    late: int = 0,
    start: datetime = NOW - timedelta(minutes=50),
) -> None:
    """``rows`` lines of ``name`` received in the last hour whose device time is ``offset``
    from the receive time (``late`` more lines received with a 10-minute delay), and the
    search counts that say its files hold rows of ``table``."""
    if session.get(Source, name) is None:
        session.add(Source(name=name, type=kind, address="172.31.255.1"))
    session.add(
        SearchCount(hour=start.replace(minute=0), source=name, raw_day=DAY, kind="traffic",
                    tbl=table, n=rows + late)
    )  # fmt: skip
    for k in range(rows + late):
        received = start + timedelta(minutes=2 * k)
        lag = offset if k < rows else -timedelta(minutes=10)
        session.add(
            Message(time=received + lag, time_received=received, source=name,
                    sender="172.31.255.1", raw_day=DAY, raw_line=k + 1, raw_offset=100 * k,
                    type="traffic", text="")
        )  # fmt: skip


@pytest.mark.parametrize(
    ("offset", "kind", "table"),
    [
        (-timedelta(minutes=8), "fortigate", "fg_traffic"),
        (-timedelta(hours=3), "fortigate", "fg_dhcp"),
        (timedelta(hours=3), "fortigate", "fg_traffic"),
        (-timedelta(minutes=8), "mikrotik", "mt_dhcp"),
    ],
)
def test_a_source_whose_clock_is_off(
    check: Any, db_session: Session, offset: timedelta, kind: str, table: str
) -> None:
    _clock(db_session, "core-r1" if kind == "mikrotik" else "fortigate", offset, kind=kind,
           table=table)  # fmt: skip
    alert = check()["R20-lease:source-clock"]
    seconds = int(offset.total_seconds())
    assert alert.severity == "high"
    assert (alert.details["seconds"], alert.details["sources"]) == (
        seconds, ["core-r1" if kind == "mikrotik" else "fortigate"],
    )  # fmt: skip
    mail = render_text([_item(alert)], "https://arcilogger.example.com")
    assert f"({seconds} sn)" in mail
    assert "fortigate" not in mail.replace("FortiGate", "")
    assert "core-r1" not in mail


def test_a_clock_alert_resolves_at_the_next_good_measurement(
    check: Any, db_session: Session
) -> None:
    _clock(db_session, "fortigate", -timedelta(minutes=8))
    assert "R20-lease:source-clock" in check()
    later = NOW + timedelta(hours=2)
    _clock(db_session, "fortigate", timedelta(milliseconds=3), start=later - timedelta(minutes=50))
    assert "R20-lease:source-clock" not in check(later)
    assert resolved(db_session)["R20-lease:source-clock"] == "clock_ok"


def test_clocks_that_are_not_judged(check: Any, db_session: Session) -> None:
    # A few late lines among on-time ones: the median ignores them.
    _clock(db_session, "fortigate", timedelta(milliseconds=3), late=4)
    # Receive-time rows (no usable device time) say nothing about the clock.
    _clock(db_session, "fortigate-2", timedelta(0))
    # A switch 10 minutes off whose files hold no row a lookup reads.
    _clock(db_session, "switch-1", -timedelta(minutes=10), kind="generic", table="")
    # A source with too few recent rows.
    _clock(db_session, "fortigate-3", -timedelta(minutes=8), rows=9)
    assert "R20-lease:source-clock" not in check()


def test_the_worker_pins_the_ingest_clock_skew() -> None:
    from arcilogger_ingest.parsers.syslog import DEFAULT_MAX_CLOCK_SKEW

    assert system.MAX_CLOCK_SKEW == DEFAULT_MAX_CLOCK_SKEW


# -- renewals -------------------------------------------------------------------------------------


def _clients(
    evidence: Evidence,
    *,
    network: str = "172.20.42",
    interface: str = "vlan42",
    lease: int = 3600,
    away: tuple[int, int] | None = None,
    count: int = 4,
) -> None:
    """``count`` clients ACKed five hours ago with ``lease`` and no renewal, each sending
    traffic every 10 minutes since (``away``: minutes after the ACK when it left and came
    back)."""  # fmt: skip
    start = NOW - timedelta(hours=5)
    for n in range(count):
        ip, mac = f"{network}.{80 + n}", f"00:00:5e:00:53:{80 + n:02x}"
        evidence.fortigate_dhcp(start, ip, mac, interface=interface, lease=lease)
        minute = 10
        while start + timedelta(minutes=minute) < NOW:
            if away is None or not away[0] <= minute < away[1]:
                evidence.traffic(start + timedelta(minutes=minute), ip, 40000 + minute)
            minute += 10


def _lease_runs(session: Session, renewals: dict[datetime, int] | None = None) -> None:
    """Build the leases in several incremental runs, recording each run as the service does;
    ``renewals``: earlier runs that counted renewal ACKs."""
    from arcilogger_worker.correlation.leases import run_incremental

    session.commit()
    moment = NOW - timedelta(hours=6)
    while moment <= NOW:
        report = run_incremental(session, now=moment)
        session.add(JobRun(job="leases.incremental", started_at=moment, finished_at=moment,
                           status="ok", details=report.details()))  # fmt: skip
        session.commit()
        moment += timedelta(minutes=45)
    for at, count in (renewals or {}).items():
        session.add(JobRun(job="leases.incremental", started_at=at, finished_at=at, status="ok",
                           details={"stats": {"fg_renewal_ack": count}}))  # fmt: skip
    session.commit()


def test_renewals_that_never_appear(check: Any, db_session: Session, evidence: Evidence) -> None:
    _clients(evidence)
    _lease_runs(db_session)
    leases = db_session.scalars(select(Lease)).all()
    assert {(lease.end_reason, lease.lease_seconds) for lease in leases} == {("timeout", 3600)}
    alert = check()["R20-lease:fortigate-renewals-missing"]
    assert (alert.severity, alert.details["count"]) == ("high", 4)
    assert "R20-lease:fortigate-refused" in alert.details["action_tr"]
    for text in (alert.details["action_tr"], alert.details["action_en"]):
        assert "fortigate_traffic_extends" not in text


@pytest.mark.parametrize(
    "case",
    [
        {"network": "172.20.61", "interface": "vlan61", "away": (40, 90)},  # guests' phones
        {"lease": 172800 // 48, "away": (50, 300)},  # back long after the lease ended
    ],
)
def test_clients_that_left_are_no_missing_renewal(
    check: Any, db_session: Session, evidence: Evidence, case: dict[str, Any]
) -> None:
    _clients(evidence, **case)
    _lease_runs(db_session)
    alerts = check()
    assert "R20-lease:fortigate-renewals-missing" not in alerts
    assert "R20-lease:fortigate-renewals-not-logged" not in alerts


def test_renewals_seen_elsewhere_or_a_verified_site(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    _clients(evidence)
    _lease_runs(db_session, renewals={NOW - timedelta(days=2): 3})
    alerts = check()
    assert "R20-lease:fortigate-renewals-missing" not in alerts
    # A verified site that has never logged a renewal: the accepted set-up, info only.
    db_session.execute(update(JobRun).values(details={}))
    set_setting(db_session, "dhcp.fortigate_format_verified", True, updated_by="tolga")
    alerts = check()
    assert alerts["R20-lease:fortigate-renewals-not-logged"].severity == "info"
    assert "R20-lease:fortigate-renewals-missing" not in alerts
    # The same site whose renewals stopped after being seen ten days ago: high.
    db_session.add(JobRun(job="leases.nightly", started_at=NOW - timedelta(days=10),
                          status="ok", details={"stats": {"fg_renewal_ack": 5}}))  # fmt: skip
    alerts = check()
    assert alerts["R20-lease:fortigate-renewals-missing"].severity == "high"


def test_unlimited_leases_are_never_counted(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    _clients(evidence, lease=0)
    _lease_runs(db_session)
    assert "R20-lease:fortigate-renewals-missing" not in check()


# -- srcmac ---------------------------------------------------------------------------------------


def test_a_shared_srcmac_per_listed_interface(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    set_setting(db_session, "lookup.srcmac_interfaces", ["vlan42", "vlan43"], updated_by="tolga")
    at = NOW - timedelta(minutes=20)
    for port, ip in ((50001, "172.20.42.57"), (50002, "172.20.42.58")):
        evidence.traffic(at, ip, port, srcintf="vlan42", srcmac="00:00:5e:00:53:01")
    # One MAC, one IPv4 and two IPv6 addresses (a dual-stack client): not shared.
    for port, ip in ((50003, "172.20.43.57"), (50004, "2001:db8::57"), (50005, "2001:db8::58")):
        evidence.traffic(at, ip, port, srcintf="vlan43", srcmac="00:00:5e:00:53:02",
                         transip=None if ":" in ip else "100.64.12.9")  # fmt: skip
    alerts = check()
    alert = alerts["R20-lease:srcmac-shared:vlan42"]
    assert alert.severity == "medium"
    assert (alert.details["count"], alert.details["most"], alert.details["limit"]) == (1, 2, 1)
    assert alert.title == "srcmac arayüzünde paylaşılan MAC adresi: vlan42"
    assert "R20-lease:srcmac-shared:vlan43" not in alerts
    assert "00:00:5e" not in str(alert.details)


# -- the 24 hours without the problem -------------------------------------------------------------


def test_a_cause_resolves_after_a_clean_day(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.57", "00:00:5e:00:53:57", lease=0)
    key = "R20-lease:fortigate-lease-unlimited"
    assert key in check()
    assert key in check(NOW + timedelta(hours=12))  # the row is still in the window
    assert key in check(NOW + timedelta(hours=30))  # gone, but not for a day yet
    assert key not in check(NOW + timedelta(hours=37))
    assert resolved(db_session)[key] == "clean_24h"


# -- internet exits -------------------------------------------------------------------------------

EXITS = {"hat-1": {"mode": "isp_nat", "public": ["198.51.100.77"], "transips": ["100.64.12.9"]}}


def test_a_failed_conversion_until_the_exits_are_written(check: Any, db_session: Session) -> None:
    db_session.add(Setting(key="nat.egresses_migration", value={"conversion_failed": True},
                           updated_by=MIGRATION_AUTHOR))  # fmt: skip
    set_setting(db_session, "nat.egresses", {}, updated_by=MIGRATION_AUTHOR)
    alert = check()["R20-nat:conversion-failed"]
    assert (alert.rule, alert.severity) == ("R20-nat", "high")
    set_setting(db_session, "nat.egresses", EXITS, updated_by="tolga")
    assert "R20-nat:conversion-failed" not in check()
    assert resolved(db_session)["R20-nat:conversion-failed"] == "exits_written"


def test_an_unclaimed_nat_address_in_use(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    set_setting(db_session, "nat.egresses", EXITS, updated_by="tolga")
    at = NOW - timedelta(minutes=30)
    for n in range(49):
        evidence.traffic(at, "172.20.42.57", 50000 + n, transip="100.64.13.9")
    assert "R20-nat:unmapped-transip" not in check()
    evidence.traffic(at, "172.20.42.57", 50099, transip="100.64.13.9")
    for n in range(60):  # the exit's own address
        evidence.traffic(at, "172.20.42.58", 51000 + n, transip="100.64.12.9")
    alert = check()["R20-nat:unmapped-transip"]
    assert alert.severity == "medium"
    assert alert.details["count"] == 50
    assert alert.details["addresses"] == [{"address": "100.64.13.9", "kind": "private"}]
    assert "100.64.13.9 (özel/paylaşılan)" in alert.details["action_tr"]
    mail = render_text([_item(alert)], "https://arcilogger.example.com")
    assert "100.64.13.9" not in mail
    set_setting(db_session, "nat.egresses",
                {**EXITS, "hat-2": {"mode": "isp_nat", "transips": ["100.64.13.9"]}},
                updated_by="tolga")  # fmt: skip
    assert "R20-nat:unmapped-transip" not in check()
    assert resolved(db_session)["R20-nat:unmapped-transip"] == "claimed"


def test_an_unreadable_exit_value_leaves_every_lease_cause_working(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    set_setting(db_session, "nat.egresses", "not an object", updated_by="tolga")
    evidence.fortigate_dhcp(NOW - timedelta(days=5), "172.20.42.50", "00:00:5e:00:53:50")
    evidence.fortigate_dhcp(NOW - HOUR, "172.20.42.57", "00:00:5e:00:53:57", dhcp_msg="DHCPACK")
    _fg_event(db_session, {"ip": "172.20.42.59", "dhcp_msg": "Ack"})
    office_traffic(evidence)
    alerts = check()
    assert alerts["R20-nat:invalid"].severity == "high"
    assert alerts["R20-nat:invalid"].details["code"] == "setting_type"
    for cause in ("none:fg_dhcp", "fortigate-refused", "fortigate-unconverted"):
        assert f"R20-lease:{cause}" in alerts
    # Everything fixed a day later: every cause resolves in the same run.
    later = NOW + timedelta(days=1, hours=1)
    set_setting(db_session, "nat.egresses", EXITS, updated_by="tolga")
    set_setting(db_session, "dhcp.fortigate_dhcp_msgs", {}, updated_by="tolga")
    db_session.execute(update(FgEvent).values(logid="0100026003"))
    evidence.fortigate_dhcp(later - HOUR, "172.20.42.57", "00:00:5e:00:53:57")
    alerts = check(later)
    assert sorted(key for key in alerts if key.startswith(("R20-nat", "R20-lease"))) == [
        "R20-lease:fortigate-unverified"
    ]
    assert {
        key: reason for key, reason in resolved(db_session).items() if "unverified" not in key
    } == {
        "R20-nat:invalid": "parsed",
        "R20-lease:none:fg_dhcp": "evidence_arrived",
        "R20-lease:fortigate-refused": "no_refused_rows",
        "R20-lease:fortigate-unconverted": "clean_24h",
    }


def _legacy(session: Session, key: str, value: Any, at: datetime) -> None:
    session.add(Setting(key=key, value=value, updated_by="tolga"))
    session.flush()
    session.execute(update(Setting).where(Setting.key == key).values(updated_at=at))


@pytest.mark.parametrize("converted", [True, False])
def test_legacy_rows_changed_during_a_rollback(
    check: Any, db_session: Session, converted: bool
) -> None:
    """Rollback, a legacy edit in the previous version, re-upgrade: named until an
    administrator writes the exits (also on a site that had the shipped NAT defaults)."""
    author = MIGRATION_AUTHOR if converted else SEED_AUTHOR
    set_setting(db_session, "nat.egresses", EXITS if converted else {}, updated_by=author)
    written = update(Setting).where(Setting.key == "nat.egresses")
    db_session.execute(written.values(updated_at=NOW - timedelta(days=9)))
    if converted:
        db_session.add(Setting(key="nat.egresses_migration", value={"conversion_failed": False},
                               updated_by=MIGRATION_AUTHOR))  # fmt: skip
        _legacy(db_session, "lookup.port_hint", True, NOW - timedelta(days=10))  # before: no
    _legacy(db_session, "nat.wan_transip", "100.64.12.10", NOW - timedelta(days=2))
    alert = check()["R20-nat:legacy-changed"]
    assert (alert.severity, alert.details["keys"]) == ("medium", ["nat.wan_transip"])
    set_setting(db_session, "nat.egresses", EXITS, updated_by="tolga")
    assert "R20-nat:legacy-changed" not in check()
    assert db_session.get(Setting, "nat.wan_transip") is not None  # the rows stay, unread


# -- e-mails and the catalogue --------------------------------------------------------------------


def test_a_high_cause_is_mailed_with_its_line_and_counts_only(
    check: Any, db_session: Session, evidence: Evidence
) -> None:
    db_session.add(Source(name="unknown-192.0.2.7", type="fortigate", address="192.0.2.7",
                          unknown=True))  # fmt: skip
    for n in range(12):
        evidence.fortigate_dhcp(NOW - HOUR, f"172.20.42.{60 + n}", f"00:00:5e:00:53:{60 + n:02x}",
                                source="unknown-192.0.2.7", logid="0100026099",
                                interface="port 9")  # fmt: skip
    alert = check()["R20-lease:fortigate-refused"]
    mail = render_text([_item(alert)], "https://arcilogger.example.com")
    assert "R20-lease — Kira kanıtı sorunu" in mail
    assert f"{LEASE_CAUSES['fortigate-refused'][0]} (12 kayıt)" in mail
    assert f"{LEASE_CAUSES['fortigate-refused'][1]} (12 records)" in mail
    for hidden in (
        "unknown-192.0.2.7",
        "192.0.2.7",
        "0100026099",
        "port 9",
        "172.20.42.",
        "00:00:5e",
    ):
        assert hidden not in mail, hidden


def test_every_cause_has_its_line_texts_and_runbook() -> None:
    assert set(LEASE_TEXTS) == set(LEASE_CAUSES)
    assert set(NAT_TEXTS) == set(NAT_CAUSES)
    for rule, causes in (("R20-lease", LEASE_CAUSES), ("R20-nat", NAT_CAUSES)):
        assert rule in RULES
        assert runbook(rule)[0].startswith("1. ")
        for cause, (line_tr, line_en) in causes.items():
            assert describe(rule, {"cause": cause}) == (line_tr, line_en)
            assert "{" not in line_tr + line_en
    for catalogue in (LEASE_TEXTS, NAT_TEXTS):
        for cause, texts in catalogue.items():
            names = [{n for _, n, _, _ in Formatter().parse(t) if n} for t in texts]
            assert names[0] == names[1], cause
            assert names[2] == names[3], cause
    assert describe("R20-lease", {"cause": "none", "table": "mt_arp"})[0].endswith(
        "(MikroTik ARP-LOG)"
    )
    assert describe("R20-lease", {"cause": "other"}) == describe("R20-lease", {})


def test_no_e_mail_or_report_line_carries_an_address_a_name_or_a_log_value() -> None:
    """2.16 and the round-5 note: scheduled reports list alerts through the same describe(),
    so nothing but the cause's line, counts and VLAN numbers leaves the server."""
    leaky = {
        "count": 3,
        "vlan": 42,
        "rows": 7,
        "addresses": ["172.20.42.57", {"address": "100.64.13.9", "kind": "private"}],
        "sources": ["unknown-192.0.2.7"],
        "source": "unknown-192.0.2.7",
        "interfaces": ["port9"],
        "interface": "port9",
        "logids": ["0100026099"],
        "dhcp_msgs": ["Nak"],
        "origins": ["FortiGate DHCP (VDOM root)"],
        "network": "Ofis (172.20.70.0/24)",
        "first_day": "2026-10-14",
        "keys": ["nat.wan_transip"],
        "code": "setting_type",
        "offsets": {"fortigate": -480},
    }
    for rule, causes in (("R20-lease", LEASE_CAUSES), ("R20-nat", NAT_CAUSES)):
        for cause in causes:
            for sentence in describe(rule, {**leaky, "cause": cause}):
                for value in (
                    "172.20",
                    "100.64",
                    "192.0.2",
                    "port9",
                    "0100026099",
                    "Nak",
                    "VDOM",
                    "Ofis",
                    "14.10",
                    "nat.wan",
                    "setting_type",
                    "fortigate",
                ):
                    assert value not in sentence, (cause, value)  # fmt: skip
