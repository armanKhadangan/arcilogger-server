"""Golden usage datasets (spec 17 M4 "golden usage datasets match"): every number by hand.

The site is :func:`arcilogger_worker.usage.testing.build_golden_site` (14.10.2026, a Wednesday;
work hours 08:00-18:00; ``usage.active_bytes_threshold`` 2048 bytes per minute). Times are
site time (+03:00); "minute 540" is 09:00 (minutes after local midnight).

**Ayşe — PC-01, 172.20.43.37** (work hours unless noted)

* S1 09:00-09:05 (www.video.example, web filter, ``sessionid`` 1001): interim 09:02 adds
  12000 + 1200 bytes over [09:00, 09:02] → 6600 in 09:00 and 09:01; interim 09:04 adds
  6000 + 600 over [09:02, 09:04] → 3300 in 09:02 and 09:03; the final log (18600 / 1860 in
  total) adds the remainder 600 + 60 over [09:04, 09:05] → 660 in 09:04.
* S2 09:02-09:03 to 198.51.100.20, named by the DNS answer of 09:01:30 (mail.posta.example):
  3000 + 300 → 3300 in 09:02.
* Active minutes (≥ 2048 non-background bytes): 09:00 (6600), 09:01 (6600), 09:02 (3300 + 3300),
  09:03 (3300) = 4 → 240 s; 09:04 has only 660. Minute 09:02 is split 30 s / 30 s, so
  video.example 60 + 60 + 30 + 60 = 210 s and posta.example 30 s (sum = active time); the tie
  makes posta.example the dominant domain of 09:02 (alphabetical): timeline runs
  [540-542 video], [542-543 posta], [543-544 video].
* Background, never active: S3 download.windowsupdate.com (pattern ``*.windowsupdate.com``)
  606000 bytes over [09:58, 10:00]; S4 tracker.reklam.example 1200 bytes (< 2048) at 10:30;
  S5 8.8.8.8:53 (DNS port) 5100 bytes [10:30:59, 10:31]; S6 no host name, application category
  ``Update`` (service label Microsoft-Azure) 51000 bytes [10:59, 11:00]. S1's category
  ``Network.Service`` does not make it background: it has a host name.
* Online minutes: 09:00-09:04 (5), 09:58, 09:59, 10:30, 10:59 = 9 → 540 s.
* Bytes: download 18600 + 3000 + 600000 + 900 + 5000 + 50000 = 677500; upload 1860 + 300 + 6000
  + 300 + 100 + 1000 = 9560. Sessions S1-S6 = 6.
* Blocked: web 11:15 (bahis.example, Kumar), DNS 11:15:01 (bahis.example), application control
  11:20 (BitTorrent, P2P) = 3.
* Off hours: S7 19:30-19:32 haber.example 12000 bytes → 6000 in 19:30 and 19:31: online 120 s,
  active 120 s, download 10000, upload 2000, 1 session.

**PC-02 (nobody), 172.20.43.81**

* S13 15:00-15:06, bulut.example: the interim of 15:02 adds 4000 over [15:00, 15:02] (2000 per
  minute: below the threshold); the interim of 15:04 is lost, so the final log's remainder
  16000 - 4000 = 12000 covers [15:02, 15:06] → 3000 per minute: active 15:02-15:05 = 240 s,
  online 15:00-15:05 = 360 s, download 16000.
* S8 23:59 → 00:01 (15.10), www.muzik.example, 6000 bytes: 3000 in 23:59 (off hours, active
  60 s) on the 14th and 3000 in 00:00 on the 15th.

**PC-03, 172.20.42.82: Burak until 12:00, then Can**

* S9 11:00-11:02 portal.example 12000 → 6000 in 11:00 and 11:01 (Burak); S10 11:59-12:01
  8000 → 4000 in 11:59 (Burak) and 4000 in 12:00 (Can) — the session counts for both; S11
  13:00-13:01 3000 (Can).
* Burak: online = active = 180 s, download 16000, 2 sessions. Can: 120 s, 7000, 2 sessions.

**172.20.41.77 (no lease)**: 14:00-14:01, 5000 bytes to 203.0.113.110 (no name: kind ``ip``):
online = active = 60 s.

**Company** (5 subjects): online 660 + 420 + 180 + 120 + 60 = 1440 s; active 360 + 300 + 180 +
120 + 60 = 1020 s; download 687500 + 19000 + 16000 + 7000 + 5000 = 734500; upload 11560;
sessions 7 + 2 + 2 + 2 + 1 = 14; blocked 3; off-hours active 120 + 60 = 180 s.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.assignments import Planner, apply, load_history, lock_devices
from arcilogger_db.models import (
    DomainCategory,
    UsageAppDaily,
    UsageBlocked,
    UsageDaily,
    UsageDay,
    UsageDeviceDaily,
    UsageDomainDaily,
    UsageHourly,
    UsageUrl,
)
from arcilogger_worker.correlation.leases import rebuild_days
from arcilogger_worker.testing import Evidence, tr
from arcilogger_worker.usage.engine import source_ips
from arcilogger_worker.usage.jobs import rebuild_day
from arcilogger_worker.usage.query import Directory
from arcilogger_worker.usage.testing import (
    BARE,
    DAY,
    NEXT,
    PC1,
    PC1_MAC,
    PC2_MAC,
    PC3_MAC,
    GoldenSite,
    build_golden_site,
)

#: After the late window of both days: the rollups are final.
NOW = tr(NEXT + timedelta(days=1), 9)


@pytest.fixture
def site(db_session: Session, layout: Layout) -> GoldenSite:
    golden = build_golden_site(db_session, layout)
    rebuild_day(db_session, DAY, now=NOW)
    rebuild_day(db_session, NEXT, now=NOW)
    return golden


def _rows(session: Session, model: Any, *columns: str, **where: Any) -> list[dict[str, Any]]:
    query = select(model)
    for name, value in where.items():
        query = query.where(getattr(model, name) == value)
    found = []
    for row in session.scalars(query):
        found.append({name: getattr(row, name) for name in columns})
    return sorted(found, key=lambda item: [str(item[c]) for c in columns])


DAILY = (
    "subject",
    "off_hours",
    "kind",
    "online_seconds",
    "active_seconds",
    "download_bytes",
    "upload_bytes",
    "sessions",
    "blocked",
    "first_activity",
    "last_activity",
    "timeline",
)


def test_daily_metrics_match_the_hand_computed_values(
    db_session: Session, site: GoldenSite
) -> None:
    ayse, burak, can = f"person:{site.ayse}", f"person:{site.burak}", f"person:{site.can}"
    expected = [
        # Ayşe, work hours: 9 online minutes, 4 active, S1-S6, 3 blocked.
        (ayse, False, "person", 540, 240, 677500, 9560, 6, 3, tr(DAY, 9), tr(DAY, 9, 4),
         [[540, 542, "video.example"], [542, 543, "posta.example"], [543, 544, "video.example"]]),
        # Ayşe, off hours: S7 19:30-19:32.
        (ayse, True, "person", 120, 120, 10000, 2000, 1, 0, tr(DAY, 19, 30), tr(DAY, 19, 32),
         [[1170, 1172, "haber.example"]]),
        (burak, False, "person", 180, 180, 16000, 0, 2, 0, tr(DAY, 11), tr(DAY, 12),
         [[660, 662, "portal.example"], [719, 720, "portal.example"]]),
        (can, False, "person", 120, 120, 7000, 0, 2, 0, tr(DAY, 12), tr(DAY, 13, 1),
         [[720, 721, "portal.example"], [780, 781, "portal.example"]]),
        (f"device:{PC2_MAC}", False, "device", 360, 240, 16000, 0, 1, 0, tr(DAY, 15, 2),
         tr(DAY, 15, 6), [[902, 906, "bulut.example"]]),
        (f"device:{PC2_MAC}", True, "device", 60, 60, 3000, 0, 1, 0, tr(DAY, 23, 59),
         tr(NEXT, 0, 0), [[1439, 1440, "muzik.example"]]),
        (f"ip:{BARE}", False, "ip", 60, 60, 5000, 0, 1, 0, tr(DAY, 14), tr(DAY, 14, 1),
         [[840, 841, "203.0.113.110"]]),
    ]  # fmt: skip
    rows = _rows(db_session, UsageDaily, *DAILY, day=DAY)
    assert rows == sorted(
        (dict(zip(DAILY, values, strict=True)) for values in expected),
        key=lambda item: [str(item[c]) for c in DAILY],
    )
    # The session over midnight: its second half belongs to the 15th (00:00, off hours).
    (next_day,) = _rows(db_session, UsageDaily, *DAILY, day=NEXT)
    assert next_day == dict(
        zip(
            DAILY,
            (f"device:{PC2_MAC}", True, "device", 60, 60, 3000, 0, 1, 0, tr(NEXT, 0),
             tr(NEXT, 0, 1), [[0, 1, "muzik.example"]]),
            strict=True,
        )
    )  # fmt: skip


def test_time_per_site_adds_up_to_the_active_time(db_session: Session, site: GoldenSite) -> None:
    ayse = f"person:{site.ayse}"
    columns = (
        "domain",
        "off_hours",
        "kind",
        "seconds",
        "raw_seconds",
        "download_bytes",
        "upload_bytes",
        "sessions",
        "background",
        "hosts",
    )
    expected = [
        ("video.example", False, "domain", 210.0, 300, 18600, 1860, 1, False,
         {"www.video.example": [210.0, 20460, 1]}),
        ("posta.example", False, "domain", 30.0, 60, 3000, 300, 1, False,
         {"mail.posta.example": [30.0, 3300, 1]}),
        ("windowsupdate.com", False, "domain", 0.0, 120, 600000, 6000, 1, True,
         {"download.windowsupdate.com": [0.0, 606000, 1]}),
        ("reklam.example", False, "domain", 0.0, 60, 900, 300, 1, True,
         {"tracker.reklam.example": [0.0, 1200, 1]}),
        ("8.8.8.8", False, "ip", 0.0, 60, 5000, 100, 1, True, {"8.8.8.8": [0.0, 5100, 1]}),
        ("Microsoft-Azure", False, "service", 0.0, 60, 50000, 1000, 1, True,
         {"Microsoft-Azure": [0.0, 51000, 1]}),
        ("haber.example", True, "domain", 120.0, 120, 10000, 2000, 1, False,
         {"haber.example": [120.0, 12000, 1]}),
    ]  # fmt: skip
    rows = _rows(db_session, UsageDomainDaily, *columns, day=DAY, subject=ayse)
    assert rows == sorted(
        (dict(zip(columns, values, strict=True)) for values in expected),
        key=lambda item: [str(item[c]) for c in columns],
    )
    work = [row for row in rows if not row["off_hours"]]
    assert sum(row["seconds"] for row in work) == 240  # = the active time of work hours
    # Every subject: the times per site add up to its active time.
    totals: dict[str, float] = {}
    for row in db_session.scalars(select(UsageDomainDaily).where(UsageDomainDaily.day == DAY)):
        totals[row.subject] = totals.get(row.subject, 0.0) + row.seconds
    active: dict[str, int] = {}
    for daily in db_session.scalars(select(UsageDaily).where(UsageDaily.day == DAY)):
        active[daily.subject] = active.get(daily.subject, 0) + daily.active_seconds
    assert {key: round(value, 6) for key, value in totals.items()} == active


def test_reassignment_splits_a_session_between_the_two_persons(
    db_session: Session, site: GoldenSite
) -> None:
    columns = ("subject", "off_hours", "online_seconds", "active_seconds", "sessions")
    rows = _rows(db_session, UsageDeviceDaily, *columns, day=DAY, mac=PC3_MAC)
    assert rows == [
        {
            "subject": f"person:{site.burak}",
            "off_hours": False,
            "online_seconds": 180,
            "active_seconds": 180,
            "sessions": 2,
        },
        {
            "subject": f"person:{site.can}",
            "off_hours": False,
            "online_seconds": 120,
            "active_seconds": 120,
            "sessions": 2,
        },
    ]
    portal = _rows(
        db_session,
        UsageDomainDaily,
        "subject",
        "seconds",
        "download_bytes",
        "sessions",
        day=DAY,
        domain="portal.example",
    )
    assert portal == [
        {
            "subject": f"person:{site.burak}",
            "seconds": 180.0,
            "download_bytes": 16000,
            "sessions": 2,
        },
        {"subject": f"person:{site.can}", "seconds": 120.0, "download_bytes": 7000, "sessions": 2},
    ]


def test_hourly_rows(db_session: Session, site: GoldenSite) -> None:
    columns = (
        "hour",
        "off_hours",
        "online_seconds",
        "active_seconds",
        "download_bytes",
        "upload_bytes",
        "top_domain",
    )
    rows = _rows(db_session, UsageHourly, *columns, day=DAY, subject=f"person:{site.ayse}")
    assert rows == [
        # 09:00-09:59: S1, S2 and S3 (09:58-09:59): 7 online minutes.
        dict(zip(columns, (tr(DAY, 9), False, 420, 240, 621600, 8160, "video.example"),
                 strict=True)),
        dict(zip(columns, (tr(DAY, 10), False, 120, 0, 55900, 1400, None), strict=True)),
        dict(zip(columns, (tr(DAY, 19), True, 120, 120, 10000, 2000, "haber.example"),
                 strict=True)),
    ]  # fmt: skip


def test_blocked_attempts_urls_and_categories(db_session: Session, site: GoldenSite) -> None:
    ayse = f"person:{site.ayse}"
    blocked = _rows(
        db_session,
        UsageBlocked,
        "time",
        "source",
        "action",
        "domain",
        "hostname",
        "category",
        "url",
        "app",
        day=DAY,
        subject=ayse,
    )
    assert blocked == [
        {"time": tr(DAY, 11, 15), "source": "web", "action": "blocked",
         "domain": "bahis.example", "hostname": "bahis.example", "category": "Kumar",
         "url": "/canli", "app": None},
        {"time": tr(DAY, 11, 15, 1), "source": "dns", "action": "block",
         "domain": "bahis.example", "hostname": "bahis.example", "category": "Kumar",
         "url": None, "app": None},
        {"time": tr(DAY, 11, 20), "source": "app", "action": "block", "domain": None,
         "hostname": None, "category": "P2P", "url": None, "app": "BitTorrent"},
    ]  # fmt: skip
    # The device rows count them too (every one, not only the stored ones).
    devices = _rows(db_session, UsageDeviceDaily, "mac", "off_hours", "blocked", subject=ayse)
    assert devices == [
        {"mac": PC1_MAC, "off_hours": False, "blocked": 3},
        {"mac": PC1_MAC, "off_hours": True, "blocked": 0},
    ]
    urls = _rows(
        db_session,
        UsageUrl,
        "url",
        "hostname",
        "domain",
        "hits",
        "first_seen",
        "last_seen",
        "category",
        "blocked",
        "off_hours",
        day=DAY,
        subject=ayse,
    )
    assert urls == [
        {"url": "/canli", "hostname": "bahis.example", "domain": "bahis.example", "hits": 1,
         "first_seen": tr(DAY, 11, 15), "last_seen": tr(DAY, 11, 15), "category": "Kumar",
         "blocked": True, "off_hours": False},
        {"url": "/gundem", "hostname": "haber.example", "domain": "haber.example", "hits": 1,
         "first_seen": tr(DAY, 19, 30), "last_seen": tr(DAY, 19, 30), "category": "Haber",
         "blocked": False, "off_hours": True},
        {"url": "/izle?v=42", "hostname": "www.video.example", "domain": "video.example",
         "hits": 2, "first_seen": tr(DAY, 9), "last_seen": tr(DAY, 9, 3, 30),
         "category": "Video", "blocked": False, "off_hours": False},
    ]  # fmt: skip
    categories = {
        row.domain: (row.category, row.cat, row.source)
        for row in db_session.scalars(select(DomainCategory))
    }
    assert categories == {
        "bahis.example": ("Kumar", 991, "web"),
        "bulut.example": ("Bulut Depolama", 907, "web"),
        "haber.example": ("Haber", 904, "web"),
        "muzik.example": ("Müzik", 906, "web"),
        "portal.example": ("İş Uygulamaları", 901, "web"),
        "video.example": ("Video", 905, "web"),
    }
    apps = _rows(
        db_session, UsageAppDaily, "app", "appcat", "seconds", "bytes", "sessions", subject=ayse
    )
    assert apps == [
        {"app": "MS.Windows.Update", "appcat": "Update", "seconds": 0.0, "bytes": 51000,
         "sessions": 1},
        {"app": "SSL_TLSv1.3.PQC", "appcat": "Network.Service", "seconds": 210.0,
         "bytes": 20460, "sessions": 1},
    ]  # fmt: skip


def test_a_device_with_blocked_attempts_only_has_a_device_row(
    db_session: Session, layout: Layout
) -> None:
    """A DNS query refused by the filter, and no traffic: the device's blocked count is kept, and
    its address and VLAN come from the blocked attempt (as for the subject row)."""
    e = Evidence(db_session, layout)
    ip, mac = "172.20.43.31", "3c:52:82:00:10:31"
    e.dhcp(tr(DAY, 7), "assigned", ip, mac, server="dhcp43", hostname="PC-31")
    e.dns(tr(DAY, 10), ip, "bahis.example", [], cat=991, catdesc="Kumar", action="block")
    db_session.commit()
    rebuild_days(db_session, DAY, NEXT, now=NOW)
    rebuild_day(db_session, DAY, now=NOW)
    columns = ("subject", "off_hours", "ip", "vlan", "online_seconds", "sessions", "blocked")
    assert _rows(db_session, UsageDeviceDaily, *columns, mac=mac) == [
        {
            "subject": f"device:{mac}",
            "off_hours": False,
            "ip": ip,
            "vlan": 43,
            "online_seconds": 0,
            "sessions": 0,
            "blocked": 1,
        }
    ]
    (daily,) = _rows(db_session, UsageDaily, "blocked", subject=f"device:{mac}")
    assert daily == {"blocked": 1}


def test_a_persons_device_with_blocked_attempts_only_keeps_its_own_address(
    db_session: Session, layout: Layout, site: GoldenSite
) -> None:
    """Ayşe's phone (VLAN 42) has only a DNS filter block while PC-01 is online: the phone's
    device row has the phone's address and VLAN, the person row keeps those of PC-01."""
    e = Evidence(db_session, layout)
    ip, mac = "172.20.42.41", "3c:52:82:00:10:41"
    e.dhcp(tr(DAY, 7), "assigned", ip, mac, server="dhcp42", hostname="TEL-41")
    e.dns(tr(DAY, 10), ip, "bahis.example", [], cat=991, catdesc="Kumar", action="block")
    db_session.commit()
    rebuild_days(db_session, DAY, NEXT, now=NOW)
    owners = lock_devices(db_session, [mac])
    planner = Planner(load_history(db_session, [mac]), now=tr(DAY, 6, 1), owners=owners)
    planner.assign(mac, site.ayse, tr(DAY, 6))
    apply(db_session, planner.changes, assigned_by="test")
    db_session.commit()
    rebuild_day(db_session, DAY, now=NOW)
    ayse = f"person:{site.ayse}"
    columns = ("mac", "off_hours", "ip", "vlan", "online_seconds", "blocked")
    assert _rows(db_session, UsageDeviceDaily, *columns, subject=ayse) == [
        {"mac": PC1_MAC, "off_hours": False, "ip": PC1, "vlan": 43, "online_seconds": 540,
         "blocked": 3},
        {"mac": PC1_MAC, "off_hours": True, "ip": PC1, "vlan": 43, "online_seconds": 120,
         "blocked": 0},
        {"mac": mac, "off_hours": False, "ip": ip, "vlan": 42, "online_seconds": 0,
         "blocked": 1},
    ]  # fmt: skip
    rows = _rows(db_session, UsageDaily, "off_hours", "ip", "vlan", "blocked", subject=ayse)
    assert rows == [
        {"off_hours": False, "ip": PC1, "vlan": 43, "blocked": 4},
        {"off_hours": True, "ip": PC1, "vlan": 43, "blocked": 0},
    ]


def test_only_private_source_addresses_are_usage(db_session: Session, layout: Layout) -> None:
    """The usage of the LAN: sources from every RFC 1918 block and from IPv6 ULA count, whichever
    block the site uses; inbound traffic from public addresses (and from 100.64.0.0/10) does not."""
    e = Evidence(db_session, layout)
    private = ["10.0.0.10", "172.16.0.10", "192.168.0.10", "fd00::10"]
    for port, ip in enumerate([*private, "100.64.0.10", "198.51.100.7"], start=50001):
        e.traffic(tr(DAY, 9), ip, port, sentbyte=100, rcvdbyte=1000)
    db_session.commit()
    lo, hi = tr(DAY, 0), tr(NEXT, 0)
    assert source_ips(db_session, lo, hi, hi) == private


def test_day_bookkeeping(db_session: Session, site: GoldenSite) -> None:
    day = db_session.get_one(UsageDay, DAY)
    assert day.status == "final"
    assert day.computed_until == tr(NEXT, 0)
    assert sorted(day.ips) == sorted([PC1, "172.20.43.81", "172.20.42.82", BARE])
    assert len(day.inputs_hash) == len(day.attribution_hash) == 64
    assert day.details["rows"]["daily"] == 7


def test_a_rebuild_is_idempotent(db_session: Session, site: GoldenSite) -> None:
    def snapshot() -> list[Any]:
        found: list[Any] = []
        for model in (UsageDaily, UsageDomainDaily, UsageHourly, UsageDeviceDaily):
            for row in db_session.scalars(select(model).where(model.day == DAY)):
                values = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                found.append(sorted((k, str(v)) for k, v in values.items()))
        return sorted(found)

    before = snapshot()
    rebuild_day(db_session, DAY, now=NOW + timedelta(hours=1))
    assert snapshot() == before


def test_today_is_partial_until_its_late_window_passed(
    db_session: Session, site: GoldenSite
) -> None:
    rebuild_day(db_session, DAY, now=tr(DAY, 20))
    day = db_session.get_one(UsageDay, DAY)
    assert day.status == "partial"
    # Up to the newest traffic log seen so far (S8 ends on the 15th: outside the day).
    assert day.computed_until == tr(DAY, 20)
    assert isinstance(day.computed_at, datetime)


def _long_session(e: Evidence, ip: str, port: int, dstip: str, sessionid: int) -> None:
    """An 80-minute session 23:00 → 00:20 (15.10) with an interim log every 10 minutes (30000
    bytes each, 3000 per minute) and a final log without deltas (240000 in total)."""
    for step in range(1, 8):  # 23:10 … 00:10
        moment = tr(DAY, 23) + timedelta(minutes=10 * step)
        e.traffic(
            moment,
            ip,
            port,
            dstip,
            sessionid=sessionid,
            interim=True,
            duration=600 * step,
            durationdelta=600,
            rcvdbyte=30000 * step,
            sentbyte=0,
            rcvddelta=30000,
            sentdelta=0,
        )
    e.traffic(
        tr(NEXT, 0, 20),
        ip,
        port,
        dstip,
        sessionid=sessionid,
        duration=4800,
        rcvdbyte=240000,
        sentbyte=0,
        action="close",
    )


def test_a_long_session_over_midnight_is_never_counted_twice(
    db_session: Session, layout: Layout
) -> None:
    """The session of :func:`_long_session`, named by a web filter log of 23:00.

    The 14th reads every log: 23:00-24:00 = 6 intervals = 180000 bytes, 60 active minutes.
    The 15th reads only the logs from 23:30 on (``usage.session_overlap_seconds`` = 30 min
    before midnight): the first of them (23:30, cumulative 90000, delta 30000) says that
    60000 bytes came before it; with the deltas of 23:30 … 00:10 (5 x 30000) the final
    remainder is 240000 - 210000 = 30000 over [00:10, 00:20]. So the 15th gets 00:00-00:20 =
    60000 bytes, 20 active minutes — not the evening's bytes again. The web filter log of
    23:00 still names the session on the 15th.
    """
    e = Evidence(db_session, layout)
    ip, mac = "172.20.43.30", "3c:52:82:00:10:30"
    e.dhcp(tr(DAY, 7), "assigned", ip, mac, server="dhcp43", hostname="PC-30")
    e.web(tr(DAY, 23), ip, 5001, "www.dizi.example", "203.0.113.120")
    _long_session(e, ip, 55001, "203.0.113.120", 5001)
    db_session.commit()
    rebuild_days(db_session, DAY, NEXT, now=NOW)
    rebuild_day(db_session, DAY, now=NOW)
    rebuild_day(db_session, NEXT, now=NOW)
    columns = ("day", "online_seconds", "active_seconds", "download_bytes", "sessions")
    rows = _rows(db_session, UsageDaily, *columns, subject=f"device:{mac}")
    assert rows == [
        {
            "day": DAY,
            "online_seconds": 3600,
            "active_seconds": 3600,
            "download_bytes": 180000,
            "sessions": 1,
        },
        {
            "day": NEXT,
            "online_seconds": 1200,
            "active_seconds": 1200,
            "download_bytes": 60000,
            "sessions": 1,
        },
    ]
    columns = ("day", "domain", "kind", "seconds", "hosts")
    assert _rows(db_session, UsageDomainDaily, *columns, subject=f"device:{mac}") == [
        {"day": DAY, "domain": "dizi.example", "kind": "domain", "seconds": 3600.0,
         "hosts": {"www.dizi.example": [3600.0, 180000, 1]}},
        {"day": NEXT, "domain": "dizi.example", "kind": "domain", "seconds": 1200.0,
         "hosts": {"www.dizi.example": [1200.0, 60000, 1]}},
    ]  # fmt: skip


def test_a_session_started_long_before_midnight_keeps_its_name(
    db_session: Session, layout: Layout
) -> None:
    """Two sessions of :func:`_long_session` named before the 15th's window (23:30): one by an
    application control log of 23:00, a background sync by a DNS answer of 22:55
    (``*.icloud.com``). The 15th looks their names up back to the sessions' start: the first is
    still uygulama.example, the sync still background (online, never active)."""
    e = Evidence(db_session, layout)
    ip, mac = "172.20.43.32", "3c:52:82:00:10:32"
    e.dhcp(tr(DAY, 7), "assigned", ip, mac, server="dhcp43", hostname="PC-32")
    e.app(tr(DAY, 23), ip, 5003, "api.uygulama.example", "203.0.113.122")
    _long_session(e, ip, 55003, "203.0.113.122", 5003)
    e.dns(tr(DAY, 22, 55), ip, "p01-content.icloud.com", ["203.0.113.123"])
    _long_session(e, ip, 55004, "203.0.113.123", 5004)
    db_session.commit()
    rebuild_days(db_session, DAY, NEXT, now=NOW)
    rebuild_day(db_session, DAY, now=NOW)
    rebuild_day(db_session, NEXT, now=NOW)
    columns = ("day", "domain", "kind", "seconds", "download_bytes", "background")
    assert _rows(db_session, UsageDomainDaily, *columns, subject=f"device:{mac}") == [
        {"day": DAY, "domain": "icloud.com", "kind": "domain", "seconds": 0.0,
         "download_bytes": 180000, "background": True},
        {"day": DAY, "domain": "uygulama.example", "kind": "domain", "seconds": 3600.0,
         "download_bytes": 180000, "background": False},
        {"day": NEXT, "domain": "icloud.com", "kind": "domain", "seconds": 0.0,
         "download_bytes": 60000, "background": True},
        {"day": NEXT, "domain": "uygulama.example", "kind": "domain", "seconds": 1200.0,
         "download_bytes": 60000, "background": False},
    ]  # fmt: skip


def test_a_lost_first_interim_log_still_counts_its_bytes(
    db_session: Session, layout: Layout
) -> None:
    """A session 10:00-10:06 with interim logs every 2 minutes (60000 bytes each) whose first
    interim log (10:02) is lost. It started inside the loaded window, so the 60000 bytes the
    10:04 log's counter holds beyond its delta came over [10:00, 10:02] (not on an earlier day):
    180000 bytes, 30000 in every minute, 6 active minutes — as with the 10:02 log."""
    e = Evidence(db_session, layout)
    ip, mac = "172.20.43.31", "3c:52:82:00:10:31"
    e.dhcp(tr(DAY, 7), "assigned", ip, mac, server="dhcp43", hostname="PC-31")
    e.traffic(
        tr(DAY, 10, 4), ip, 55002, "203.0.113.121", sessionid=5002, interim=True, duration=240,
        durationdelta=120, rcvdbyte=120000, sentbyte=0, rcvddelta=60000, sentdelta=0,
    )  # fmt: skip
    e.traffic(
        tr(DAY, 10, 6), ip, 55002, "203.0.113.121", sessionid=5002, duration=360,
        rcvdbyte=180000, sentbyte=0, action="close",
    )  # fmt: skip
    db_session.commit()
    rebuild_days(db_session, DAY, DAY, now=NOW)
    rebuild_day(db_session, DAY, now=NOW)
    columns = ("online_seconds", "active_seconds", "download_bytes", "first_activity", "timeline")
    assert _rows(db_session, UsageDaily, *columns, subject=f"device:{mac}") == [
        {"online_seconds": 360, "active_seconds": 360, "download_bytes": 180000,
         "first_activity": tr(DAY, 10), "timeline": [[600, 606, "203.0.113.121"]]},
    ]  # fmt: skip


def test_a_subject_with_only_blocked_attempts_keeps_its_vlan(
    db_session: Session, layout: Layout
) -> None:
    """An unassigned phone on VLAN 42 whose only events of the day are DNS filter blocks has no
    online minute: its VLAN (and so its department) comes from the blocked attempts."""
    e = Evidence(db_session, layout)
    ip, mac = "172.20.42.40", "3c:52:82:00:10:40"
    e.dhcp(tr(DAY, 7), "assigned", ip, mac, server="dhcp42", hostname="TEL-40")
    e.dns(tr(DAY, 10), ip, "bahis.example", [], cat=991, catdesc="Kumar", action="block")
    db_session.commit()
    rebuild_days(db_session, DAY, DAY, now=NOW)
    rebuild_day(db_session, DAY, now=NOW)
    key = f"device:{mac}"
    columns = ("online_seconds", "blocked", "ip", "vlan")
    rows = _rows(db_session, UsageDaily, *columns, subject=key)
    assert rows == [{"online_seconds": 0, "blocked": 1, "ip": ip, "vlan": 42}]
    infos = Directory(db_session).resolve({key: {"kind": "device", **rows[0]}})
    assert infos[key].department_name == "Planlama"
