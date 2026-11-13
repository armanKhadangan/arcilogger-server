"""Access and device rules R03, R04, R11, R12, R16 (Wave 6 contract §2.2.2, T-R, §0.4 V1/V2, T-A7).

The FortiGate and MikroTik rows are built from the lines of ``tests/fixtures/synthetic/``
(retimed, and with the placeholder logid replaced where a test says so) through the real
parsers of ``arcilogger_ingest``, written to the spool as the collector writes them: an evidence
reference of a hit points at a raw line that ``/raw`` can open. When a parser stops producing
the fields a rule reads, the format tests fail naming V1 or V2.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from ipaddress import ip_network
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import RawRecord, format_line
from arcilogger_core.rawlog import parse_line as parse_raw
from arcilogger_core.timeutil import SITE_TZ, site_day, to_site
from arcilogger_db.models import Base, Device, Setting
from arcilogger_ingest.parsers import LinePosition, SourceType, parse_line
from arcilogger_worker.correlation.devices import DeviceSighting, upsert_devices
from arcilogger_worker.rules.access import RULES as ACCESS_RULES
from arcilogger_worker.rules.model import Actions, Hit, RuleContext, RuleDef, RuleSettings
from arcilogger_worker.rules.texts import COMMON_FIELDS, TEXTS, title, title_fields
from arcilogger_worker.testing import Evidence, tr

from .cases import CASES, SLICE_END, CaseRow, RuleCase

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "synthetic"
#: A Wednesday (work hours 08:00 to 18:00 by default); 2026-10-29 is a shipped holiday.
DAY = date(2026, 9, 23)
HOLIDAY = date(2026, 10, 29)
FIREWALL = "172.31.255.1"
ROUTER = "172.20.10.1"
ATTACKER = "172.20.41.196"
ADMIN_PC = "172.20.10.37"
MAC_A = "00:00:5e:00:53:10"
MAC_B = "00:00:5e:00:53:11"
MAC_C = "00:00:5e:00:53:12"

RULES: dict[str, RuleDef] = {rule.id: rule for rule in ACCESS_RULES}


# -- the fixture lines -----------------------------------------------------------------------------


def _fixture(name: str, needle: str) -> str:
    """The first line of a synthetic fixture file containing ``needle`` (comments skipped)."""
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if not line.startswith("#") and needle in line:
            return line
    raise AssertionError(f"{name}: no line with {needle!r} (the fixture changed)")


FG_LOGIN_OK = _fixture("fortigate-synthetic.log", 'logdesc="Admin login successful"')
FG_LOGIN_FAILED = _fixture("fortigate-synthetic.log", 'logdesc="Admin login failed"')
FG_CONFIG = _fixture("fortigate-synthetic.log", 'logdesc="Configuration changed"')
MT_LOGIN = _fixture("mikrotik-wire-synthetic.log", "logged in from")
MT_LOGIN_FAILED = _fixture(
    "mikrotik-wire-synthetic.log", f"login failure for user admin from {ATTACKER}"
)
MT_LOGIN_FAILED_MAC = _fixture(
    "mikrotik-wire-synthetic.log", "login failure for user admin from 00:"
)
MT_CONFIG = [
    _fixture("mikrotik-wire-synthetic.log", text)
    for text in ("filter rule changed by admin", "added by admin", "removed by admin")
]


def fortigate(line: str, moment: datetime, **fields: str) -> str:
    """``line`` at ``moment`` with ``fields`` replaced (``key="value"`` or ``key=value``)."""
    local = to_site(moment)
    line = re.sub(r"date=\S+", f"date={local:%Y-%m-%d}", line)
    line = re.sub(r"time=\S+", f"time={local:%H:%M:%S}", line, count=1)
    line = re.sub(r"eventtime=\d+", f"eventtime={int(moment.timestamp()) * 10**9}", line)
    for key, value in fields.items():
        pattern = rf'(?<=\s){key}=("[^"]*"|\S+)'
        assert re.search(pattern, line), f"{key} missing in the fixture line"
        line = re.sub(pattern, f'{key}="{value}"' if key != "srcip" else f"{key}={value}", line)
    return line


def mikrotik(line: str, moment: datetime) -> str:
    stamp = to_site(moment).isoformat(timespec="milliseconds")
    return re.sub(r"^(<\d+>)\S+", rf"\g<1>{stamp}", line)


# -- writing lines as the collector and ingest would -----------------------------------------------


@dataclass
class Site:
    session: Session
    layout: Layout

    def __post_init__(self) -> None:
        self.lines: dict[tuple[str, date], int] = {}

    def add(
        self, source: str, kind: SourceType, sender: str, message: str, received: datetime
    ) -> list[tuple[str, int]]:
        """Write the raw line, parse it, insert its typed rows: ``(table, id)`` of each."""
        day = site_day(received)
        path = self.layout.spool_file(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        offset = path.stat().st_size if path.exists() else 0
        data = format_line(received, sender, message.encode("utf-8"))
        with path.open("ab") as handle:
            handle.write(data)
        number = self.lines[(source, day)] = self.lines.get((source, day), 0) + 1
        outcome = parse_line(
            RawRecord(received, sender, message.encode("utf-8")),
            LinePosition(source, kind, day, number, offset),
        )
        assert outcome.error is None, (outcome.error, outcome.detail)
        found = []
        for row in outcome.rows:
            table = Base.metadata.tables[row.table]
            row_id = self.session.execute(
                insert(table).values(**row.values).returning(table.c.id)
            ).scalar_one()
            found.append((row.table, int(row_id)))
        return found

    def fg(self, line: str, moment: datetime, **fields: str) -> list[tuple[str, int]]:
        received = moment + timedelta(milliseconds=5)
        return self.add(
            "fortigate", "fortigate", FIREWALL, fortigate(line, moment, **fields), received
        )

    def mt(self, line: str, moment: datetime) -> list[tuple[str, int]]:
        received = moment + timedelta(milliseconds=5)
        return self.add("core-r1", "mikrotik", ROUTER, mikrotik(line, moment), received)

    def raw(self, source: str, day: date, offset: int) -> bytes:
        with self.layout.spool_file(source, day).open("rb") as handle:
            handle.seek(offset)
            return parse_raw(handle.readline()).message


@pytest.fixture
def site(db_session: Session, layout: Layout) -> Site:
    return Site(db_session, layout)


def setting(session: Session, key: str, value: Any) -> None:
    session.merge(Setting(key=key, value=value, updated_by="test"))
    session.flush()


# -- running a rule -------------------------------------------------------------------------------


def run(
    session: Session,
    rule_id: str,
    end: datetime,
    *,
    start: datetime | None = None,
    params: Mapping[str, Any] | None = None,
) -> list[Hit]:
    """Evaluate one rule over the slice ``(start, end]`` (default: the last 60 s), with its
    parameters as the settings give them: lists as tuples, networks as network objects."""
    rule = RULES[rule_id]
    effective: dict[str, object] = {p.key: p.default for p in rule.params}
    for key, value in (params or {}).items():
        kind = next(p.kind for p in rule.params if p.key == key)
        if kind == "networks":
            value = tuple(ip_network(str(item)) for item in value)
        elif kind in {"strings", "ints"}:
            value = tuple(value)
        effective[key] = value
    settings = RuleSettings(
        enabled=True,
        severity=rule.severity,
        window_seconds=rule.window_seconds,
        cooldown_seconds=rule.cooldown_seconds,
        actions=Actions(email=True, webhook=True),
        params=effective,
    )
    ctx = RuleContext(
        settings=settings,
        start=end - timedelta(seconds=60) if start is None else start,
        end=end,
        max_device_skew=timedelta(hours=1),
        internal_networks=tuple(
            ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        ),
        zone=SITE_TZ,
    )
    hits = sorted(rule.evaluate(session, ctx), key=lambda hit: hit.key)
    for hit in hits:  # every hit can fill its rule's title (texts.py)
        title(rule_id, hit.key, hit.count, hit.values)
    return hits


def keys(hits: list[Hit]) -> dict[str, int]:
    return {hit.key: hit.count for hit in hits}


def assert_openable(site: Site, hit: Hit, *needles: str) -> None:
    """Every evidence reference opens a raw line; each needle is in one of them."""
    texts = []
    for ref in hit.evidence:
        assert ref.source is not None
        assert ref.raw_day is not None
        assert ref.raw_offset is not None
        texts.append(site.raw(ref.source, ref.raw_day, ref.raw_offset).decode())
    received = [ref.time_received for ref in hit.evidence if ref.time_received is not None]
    assert len(received) == len(hit.evidence)
    assert received == sorted(received)
    for needle in needles:
        assert any(needle in text for text in texts), (needle, texts)


def at(hour: int, minute: int = 0, second: int = 0, day: date = DAY) -> datetime:
    return tr(day, hour, minute, second).astimezone(UTC)


# -- R03 ------------------------------------------------------------------------------------------


def test_r03_counts_each_user_and_each_address(site: Site, db_session: Session) -> None:
    for k in range(3):
        site.fg(FG_LOGIN_FAILED, at(9, 50, k), logid="0100032002")
    for k in range(2):
        site.mt(MT_LOGIN_FAILED, at(9, 50, 10 + k))
    hits = run(db_session, "R03", at(9, 51))
    assert keys(hits) == {f"ip:{ATTACKER}": 5, "user:admin": 5}
    user = next(hit for hit in hits if hit.key == "user:admin")
    assert user.values == {"by": "user", "subject": "admin", "failures": 5, "window_seconds": 300}
    assert user.time == at(9, 50, 11) + timedelta(milliseconds=5)
    assert_openable(site, user, 'logdesc="Admin login failed"', "login failure for user admin")


def test_r03_below_the_threshold_and_outside_the_window(site: Site, db_session: Session) -> None:
    site.fg(FG_LOGIN_FAILED, at(9, 44))  # outside the 300-s window of the slice ending 09:51
    for k in range(4):
        site.fg(FG_LOGIN_FAILED, at(9, 50, k))
    assert run(db_session, "R03", at(9, 51)) == []
    site.fg(FG_LOGIN_FAILED, at(9, 50, 30))
    assert keys(run(db_session, "R03", at(9, 51))) == {f"ip:{ATTACKER}": 5, "user:admin": 5}


def test_r03_needs_a_row_in_the_slice(site: Site, db_session: Session) -> None:
    for k in range(5):
        site.fg(FG_LOGIN_FAILED, at(9, 48, k))
    assert run(db_session, "R03", at(9, 51), start=at(9, 50)) == []
    assert len(run(db_session, "R03", at(9, 51), start=at(9, 47))) == 2


def test_r03_a_failure_without_an_address_counts_for_the_user(
    site: Site, db_session: Session
) -> None:
    for k in range(5):
        site.mt(MT_LOGIN_FAILED_MAC, at(9, 50, k))
    assert keys(run(db_session, "R03", at(9, 51))) == {"user:admin": 5}


def test_r03_evidence_is_capped_oldest_first(site: Site, db_session: Session) -> None:
    for k in range(8):
        site.mt(MT_LOGIN_FAILED, at(9, 50, k))
    setting(db_session, "rules.evidence_max", 3)
    hits = run(db_session, "R03", at(9, 51))
    assert [hit.count for hit in hits] == [8, 8]
    for hit in hits:
        assert [ref.time for ref in hit.evidence] == [at(9, 50, k) for k in range(3)]


# -- R04 ------------------------------------------------------------------------------------------


def test_r04_login_off_hours(site: Site, db_session: Session) -> None:
    site.mt(MT_LOGIN, at(22, 10))
    site.fg(FG_LOGIN_OK, at(22, 10, 30), logid="0100032001")
    hits = run(db_session, "R04", at(22, 11))
    assert keys(hits) == {"user:admin": 1, "user:netops": 1}
    netops = hits[1]
    assert netops.values == {
        "srcip": ADMIN_PC,
        "reason_tr": "mesai dışı",
        "reason_en": "off hours",
        "user": "netops",
        "logins": 1,
        "off_hours": True,
        "unexpected_address": False,
    }
    assert title("R04", netops.key, netops.count, netops.values) == (
        f"Yönetici girişi: user:netops, {ADMIN_PC} (mesai dışı)",
        f"Admin login: user:netops, {ADMIN_PC} (off hours)",
    )
    assert_openable(site, netops, "user netops logged in")
    assert_openable(site, hits[0], 'logdesc="Admin login successful"')


def test_r04_work_time_and_breaks_are_not_off_hours(site: Site, db_session: Session) -> None:
    setting(
        db_session,
        "usage.work_hours",
        {
            "days": [1, 2, 3, 4, 5],
            "start": "08:00",
            "end": "18:00",
            "breaks": [{"start": "12:00", "end": "13:00"}],
        },
    )
    site.mt(MT_LOGIN, at(10, 0))
    site.mt(MT_LOGIN, at(12, 30))  # a break is work time for R04 (contract §5)
    assert run(db_session, "R04", at(10, 1)) == []
    assert run(db_session, "R04", at(12, 31)) == []
    site.mt(MT_LOGIN, at(7, 59))
    assert keys(run(db_session, "R04", at(8, 0))) == {"user:netops": 1}


def test_r04_holiday_is_off_hours(site: Site, db_session: Session) -> None:
    site.mt(MT_LOGIN, at(10, 0, day=HOLIDAY))
    assert keys(run(db_session, "R04", at(10, 1, day=HOLIDAY))) == {"user:netops": 1}
    assert run(db_session, "R04", at(10, 1, day=HOLIDAY), params={"off_hours": False}) == []


def test_r04_unexpected_address(site: Site, db_session: Session) -> None:
    trusted = {"trusted_admin_networks": ["172.20.10.0/24"], "off_hours": False}
    site.mt(MT_LOGIN, at(10, 0))  # from 172.20.10.37: trusted
    assert run(db_session, "R04", at(10, 1), params=trusted) == []
    site.fg(FG_LOGIN_OK, at(10, 1, 30), logid="0100032001", srcip=ATTACKER)
    hits = run(db_session, "R04", at(10, 2), params=trusted)
    assert keys(hits) == {"user:admin": 1}
    assert hits[0].values["unexpected_address"] is True
    assert hits[0].values["off_hours"] is False
    assert hits[0].values["reason_en"] == "unexpected address"
    assert hits[0].values["srcip"] == ATTACKER


def test_r04_failed_logins_are_not_logins(site: Site, db_session: Session) -> None:
    site.fg(FG_LOGIN_FAILED, at(22, 10), logid="0100032002")
    site.mt(MT_LOGIN_FAILED, at(22, 10, 5))
    assert run(db_session, "R04", at(22, 11)) == []


# -- R11 ------------------------------------------------------------------------------------------


def _now(session: Session) -> datetime:
    """The transaction time: ``devices.created_at`` of every row this test inserts."""
    return session.execute(select(func.now())).scalar_one()


def test_r11_reports_a_device_the_inventory_just_learnt(
    evidence: Evidence, db_session: Session
) -> None:
    seen = at(9, 40)
    evidence.dhcp(seen, "assigned", "172.20.43.77", MAC_A, server="dhcp43", vlan=43)
    upsert_devices(
        db_session, [DeviceSighting(MAC_A, seen.replace(microsecond=0), "172.20.43.77", 43)]
    )
    now = _now(db_session)
    hits = run(db_session, "R11", now + timedelta(seconds=1))
    assert keys(hits) == {MAC_A: 1}
    assert hits[0].values == {
        "mac": MAC_A,
        "ip": "172.20.43.77",
        "vlan": 43,
        "first_seen": seen.isoformat(),
    }
    assert [(ref.table, ref.source) for ref in hits[0].evidence] == [("mt_dhcp", "core-r1")]
    assert hits[0].time == now


def test_r11_never_reports_devices_known_before_the_slice(
    evidence: Evidence, db_session: Session
) -> None:
    upsert_devices(db_session, [DeviceSighting(MAC_A, at(9, 40), "172.20.43.77", 43)])
    now = _now(db_session)
    assert run(db_session, "R11", now + timedelta(minutes=2), start=now) == []


def test_r11_filters(db_session: Session) -> None:
    random_mac = "02:00:5e:00:53:20"
    upsert_devices(
        db_session,
        [
            DeviceSighting(MAC_A, at(9, 40), "172.20.10.2", 10),
            DeviceSighting(MAC_B, at(9, 40), "172.20.60.20", 60),
            DeviceSighting(random_mac, at(9, 40), "172.20.61.20", 61),
            DeviceSighting(MAC_C, at(9, 40), None, None),
        ],
        infrastructure_macs=[MAC_A],
    )
    end = _now(db_session) + timedelta(seconds=1)
    assert set(keys(run(db_session, "R11", end))) == {MAC_B, MAC_C}
    assert set(keys(run(db_session, "R11", end, params={"ignore_random": False}))) == {
        MAC_B,
        MAC_C,
        random_mac,
    }
    assert set(keys(run(db_session, "R11", end, params={"ignore_vlans": [60, 61]}))) == {MAC_C}
    db_session.execute(update(Device).where(Device.mac == MAC_B).values(infrastructure=True))
    assert set(keys(run(db_session, "R11", end))) == {MAC_C}


# -- R12 ------------------------------------------------------------------------------------------


def _flap(evidence: Evidence, ip: str, macs: list[str], first: datetime) -> None:
    for k, mac in enumerate(macs):
        evidence.arp(first + timedelta(minutes=5 * k), ip, mac)


def test_r12_counts_mac_changes_of_an_address(evidence: Evidence, db_session: Session) -> None:
    ip = "172.20.43.50"
    _flap(evidence, ip, [MAC_A, MAC_B, MAC_A, MAC_B], at(9, 0))
    hits = run(db_session, "R12", at(9, 16))
    assert keys(hits) == {ip: 3}
    assert hits[0].values == {
        "ip": ip,
        "changes": 3,
        "macs": [MAC_A, MAC_B],
        "window_seconds": 3600,
    }
    assert [ref.table for ref in hits[0].evidence] == ["mt_arp"] * 3


def test_r12_below_the_threshold(evidence: Evidence, db_session: Session) -> None:
    _flap(evidence, "172.20.43.50", [MAC_A, MAC_B, MAC_A, MAC_A, MAC_A], at(9, 0))
    assert run(db_session, "R12", at(9, 21)) == []


def test_r12_mixes_arp_and_both_dhcp_servers(evidence: Evidence, db_session: Session) -> None:
    ip = "172.20.42.60"
    evidence.arp(at(9, 0), ip, MAC_A, "vlan42")
    evidence.dhcp(at(9, 5), "assigned", ip, MAC_B, server="dhcp42", vlan=42)
    evidence.dhcp(at(9, 6), "deassigned", ip, MAC_A, server="dhcp42", vlan=42)  # not counted
    evidence.fortigate_dhcp(at(9, 10), ip, MAC_A, interface="vlan42")
    evidence.fortigate_dhcp(at(9, 11), ip, MAC_B, interface="vlan42", dhcp_msg="Nak")  # no ACK
    evidence.fortigate_dhcp(at(9, 12), ip, MAC_B, interface="vlan42")
    hits = run(db_session, "R12", at(9, 13))
    assert keys(hits) == {ip: 3}
    assert sorted({ref.table for ref in hits[0].evidence}) == ["fg_dhcp", "mt_dhcp"]
    setting(db_session, "dhcp.fortigate_interfaces_excluded", ["vlan42"])
    assert run(db_session, "R12", at(9, 13)) == []


def test_r12_ignores_infrastructure_macs(evidence: Evidence, db_session: Session) -> None:
    ip = "172.20.43.1"
    _flap(evidence, ip, [MAC_A, MAC_B, MAC_A, MAC_B], at(9, 0))
    setting(db_session, "devices.infrastructure_macs", [MAC_B.upper()])
    assert run(db_session, "R12", at(9, 16)) == []
    setting(db_session, "devices.infrastructure_macs", [])
    upsert_devices(
        db_session, [DeviceSighting(MAC_A, at(9, 0), ip, 43)], infrastructure_macs=[MAC_A]
    )
    assert run(db_session, "R12", at(9, 16)) == []


# -- R16 ------------------------------------------------------------------------------------------


def test_r16_reports_changes_per_device_and_user(site: Site, db_session: Session) -> None:
    site.fg(FG_CONFIG, at(10, 0), logid="0100044546")
    for k, line in enumerate(MT_CONFIG):
        site.mt(line, at(10, 0, 10 + k))
    hits = run(db_session, "R16", at(10, 1))
    assert keys(hits) == {"core-r1:admin": 3, "fortigate:admin": 1}
    assert hits[0].values == {"device": "core-r1", "user": "admin", "changes": 3}
    assert_openable(site, hits[0], "filter rule changed by admin", "removed by admin")
    assert_openable(site, hits[1], 'logdesc="Configuration changed"')


def test_r16_only_the_slice_and_the_configured_logids(site: Site, db_session: Session) -> None:
    site.fg(FG_CONFIG, at(10, 0, 30))  # the fixture's placeholder logid
    site.mt(MT_CONFIG[0], at(9, 59))  # before the slice
    assert run(db_session, "R16", at(10, 1)) == []
    assert keys(run(db_session, "R16", at(10, 1), params={"fortigate_logids": ["9900000010"]})) == {
        "fortigate:admin": 1
    }


# -- §0.4 V1, V2: the parser still produces what the rules read ----------------------------------


def test_r03_r04_formats(site: Site, db_session: Session) -> None:
    """V1: admin login lines (logid ``0100032002`` failed, ``0100032001`` successful, also by
    ``logdesc``) become ``fg_event`` rows with ``user_name`` and ``srcip``; MikroTik
    ``account`` lines ``mt_account`` rows with ``action`` ``login`` / ``login_failure``."""
    tables = [
        *site.fg(FG_LOGIN_FAILED, at(22, 0, 1), logid="0100032002", logdesc="sentetik"),
        *site.fg(FG_LOGIN_FAILED, at(22, 0, 2)),  # placeholder logid, matched by logdesc
        *site.mt(MT_LOGIN_FAILED, at(22, 0, 3)),
    ]
    assert [t for t, _ in tables] == ["fg_event", "fg_event", "mt_account"], "V1: parser output"
    assert keys(run(db_session, "R03", at(22, 1), params={"threshold": 3})) == {
        f"ip:{ATTACKER}": 3,
        "user:admin": 3,
    }, "V1: R03 no longer matches the FortiGate/MikroTik failed-login rows"
    site.fg(FG_LOGIN_OK, at(22, 5, 1), logid="0100032001", logdesc="sentetik")
    site.fg(FG_LOGIN_OK, at(22, 5, 2))
    site.mt(MT_LOGIN, at(22, 5, 3))
    assert keys(run(db_session, "R04", at(22, 6))) == {"user:admin": 2, "user:netops": 1}, (
        "V1: R04 no longer matches the successful-login rows"
    )


def test_r16_formats(site: Site, db_session: Session) -> None:
    """V2: configuration changes (logids ``0100044546``, ``0100044547``) become ``fg_event``
    rows with ``user_name``; MikroTik ``… by <user>`` lines ``mt_config`` rows."""
    tables = [
        *site.fg(FG_CONFIG, at(11, 0, 1), logid="0100044546"),
        *site.fg(FG_CONFIG, at(11, 0, 2), logid="0100044547"),
        *(row for line in MT_CONFIG for row in site.mt(line, at(11, 0, 3))),
    ]
    assert [t for t, _ in tables] == ["fg_event", "fg_event", *["mt_config"] * 3], (
        "V2: parser output"
    )
    assert keys(run(db_session, "R16", at(11, 1))) == {
        "core-r1:admin": 3,
        "fortigate:admin": 2,
    }, "V2: R16 no longer matches the configuration-change rows"


# -- T-A7: list parameters are data, never SQL -----------------------------------------------------

HOSTILE = ["x' OR '1'='1", "a; DROP TABLE fg_event; --", "100%", "%", "_", '"', "\\"]


def test_list_parameters_change_only_the_match(site: Site, db_session: Session) -> None:
    for k in range(5):
        site.fg(FG_LOGIN_FAILED, at(9, 50, k))
    site.fg(FG_CONFIG, at(9, 50, 30))
    plain = run(db_session, "R03", at(9, 51))
    assert keys(plain) == {f"ip:{ATTACKER}": 5, "user:admin": 5}
    hostile = run(
        db_session,
        "R03",
        at(9, 51),
        params={
            "fortigate_fail_logids": ["0100032002", *HOSTILE],
            "fortigate_fail_descs": ["Admin login failed", *HOSTILE],
        },
    )
    assert keys(hostile) == keys(plain)
    assert (
        run(
            db_session,
            "R03",
            at(9, 51),
            params={"fortigate_fail_logids": HOSTILE, "fortigate_fail_descs": HOSTILE},
        )
        == []
    )
    assert run(db_session, "R16", at(9, 51), params={"fortigate_logids": HOSTILE}) == []
    assert run(db_session, "R04", at(9, 51), params={"fortigate_ok_descs": HOSTILE}) == []
    fg_event = Base.metadata.tables["fg_event"]
    assert db_session.execute(select(func.count()).select_from(fg_event)).scalar_one() == 6


def test_rule_definitions() -> None:
    assert [rule.id for rule in ACCESS_RULES] == ["R03", "R04", "R11", "R12", "R16"]
    unverified = {rule.id for rule in ACCESS_RULES if not rule.format_verified}
    assert unverified == {"R03", "R04", "R16"}
    for rule in ACCESS_RULES:
        assert rule.kind == "event"
        for param in rule.params:
            assert param.label_tr, (rule.id, param.key)
            assert param.label_en, (rule.id, param.key)
        assert title_fields(TEXTS[rule.id]) - COMMON_FIELDS <= {"srcip", "reason_tr", "reason_en"}


# -- the W6-C block of cases.py, run on these rules -----------------------------------------------


def _write_case_row(session: Session, row: CaseRow, n: int) -> None:
    """A case row with the common columns as the case block assumes them (``core-r1`` for a
    MikroTik table, ``fortigate`` for a FortiGate one; a device's insert time = receive time)."""
    received = SLICE_END + row.received + timedelta(seconds=n)
    if row.table == "devices":
        values = {"first_seen": received, "last_seen": received, **row.values}
        session.execute(insert(Device).values(**values, created_at=received))
        return
    table = Base.metadata.tables[row.table]
    session.execute(
        insert(table).values(
            time=received + row.device_offset,
            time_received=received,
            source="core-r1" if row.table.startswith("mt_") else "fortigate",
            sender="172.20.10.1" if row.table.startswith("mt_") else FIREWALL,
            raw_day=site_day(received),
            raw_line=n + 1,
            raw_offset=0,
            **row.values,
        )
    )


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.rule in RULES],
    ids=lambda case: f"{case.rule}-{case.name}",
)
def test_the_w6c_cases(db_session: Session, case: RuleCase) -> None:
    for key, value in case.settings.items():
        setting(db_session, key, value)
    for row in case.rows:
        for n in range(row.repeat):
            _write_case_row(db_session, row, n)
    params = dict(case.overrides.get("params", {}))  # type: ignore[call-overload]
    hits = run(
        db_session,
        case.rule,
        SLICE_END,
        start=SLICE_END - timedelta(seconds=case.slice_seconds or 600),
        params=params,
    )
    assert keys(hits) == {expected.key: expected.count for expected in case.expected}
    for expected in case.expected:
        hit = next(hit for hit in hits if hit.key == expected.key)
        assert {k: hit.values[k] for k in expected.values} == dict(expected.values)
        if expected.evidence is not None:
            assert len(hit.evidence) == expected.evidence
