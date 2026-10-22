"""Golden usage dataset (spec 17 M4 "golden usage datasets match"): a small, hand-checkable site.

:func:`build_golden_site` writes, through :class:`arcilogger_worker.testing.Evidence` (raw spool
lines plus typed rows, as ingest would), one Wednesday (:data:`DAY`, 14.10.2026) of a tiny
network whose every expected metric is computed by hand in ``worker/tests/test_usage_golden.py``:

=================  ==================  =====================================================
Address            Owner               Traffic
=================  ==================  =====================================================
172.20.43.37       PC-01 → Ayşe        interim + final logs (web host name), a concurrent
                   (Finans)            session named only by DNS, background traffic (pattern,
                                       < 2 KB, DNS port, application category), blocked
                                       attempts (web, DNS, application control), an evening
                                       session (off hours), URLs
172.20.43.81       PC-02, nobody       a session over midnight into 15.10, a lost interim log
172.20.42.82       PC-03 → Burak,      reassigned to Can (Finans) at 12:00, one session across
                   then Can            the change
172.20.41.77       no lease            the bare address (VLAN 41: Finans, like Can)
=================  ==================  =====================================================

Names, addresses and domains are synthetic (``*.example``, RFC 2606; documentation ranges,
RFC 5737). Used by the worker, API and report tests and by ``web/scripts/capture_api_samples.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.assignments import Planner, apply, load_history, lock_devices
from arcilogger_db.models import Department, Device, Person
from arcilogger_worker.correlation.leases import rebuild_days
from arcilogger_worker.testing import Evidence, tr

#: The golden day (a Wednesday) and the next one (the session over midnight ends there).
DAY = date(2026, 10, 14)
NEXT = DAY + timedelta(days=1)
PC1, PC1_MAC = "172.20.43.37", "3c:52:82:00:10:01"
PC2, PC2_MAC = "172.20.43.81", "3c:52:82:00:10:02"
PC3, PC3_MAC = "172.20.42.82", "3c:52:82:00:10:03"
BARE = "172.20.41.77"
VIDEO, MAIL, NEWS = "203.0.113.10", "198.51.100.20", "203.0.113.30"


@dataclass(frozen=True)
class GoldenSite:
    ayse: int
    burak: int
    can: int


def _person(session: Session, name: str, department: str, title: str) -> int:
    department_id = session.scalars(
        select(Department.id).where(Department.name == department)
    ).one()
    person = Person(name=name, department_id=department_id, title=title)
    session.add(person)
    session.flush()
    return int(person.id)


def _assign(session: Session, mac: str, person_id: int, start: datetime) -> None:
    owners = lock_devices(session, [mac])
    planner = Planner(load_history(session, [mac]), now=start + timedelta(minutes=1), owners=owners)
    planner.assign(mac, person_id, start)
    apply(session, planner.changes, assigned_by="golden")


def build_golden_site(session: Session, layout: Layout, *, day: date = DAY) -> GoldenSite:
    """Write the golden day, rebuild the lease timeline, assign the devices; commits.

    ``day`` moves the whole dataset (the API sample capture uses the day before its clock);
    the weekday matters for the work-hour split, the tests use :data:`DAY`.
    """
    e = Evidence(session, layout)
    d = day
    following = d + timedelta(days=1)
    for ip, mac, server, host in (
        (PC1, PC1_MAC, "dhcp43", "PC-01"),
        (PC2, PC2_MAC, "dhcp43", "PC-02"),
        (PC3, PC3_MAC, "dhcp42", "PC-03"),
    ):
        e.dhcp(tr(d, 7), "assigned", ip, mac, server=server, hostname=host)

    # -- PC-01 (Ayşe) ---------------------------------------------------------------------------
    # S1: 09:00-09:05, interim logs every 2 minutes, final remainder; web host name, URL hits.
    e.web(
        tr(d, 9),
        PC1,
        1001,
        "www.video.example",
        VIDEO,
        url="/izle?v=42",
        cat=905,
        catdesc="Video",
        action="passthrough",
    )
    e.web(
        tr(d, 9, 3, 30),
        PC1,
        1001,
        "www.video.example",
        VIDEO,
        url="/izle?v=42",
        cat=905,
        catdesc="Video",
        action="passthrough",
    )
    app, appcat = "SSL_TLSv1.3.PQC", "Network.Service"
    e.traffic(
        tr(d, 9, 2),
        PC1,
        50001,
        VIDEO,
        interim=True,
        duration=120,
        durationdelta=120,
        rcvdbyte=12000,
        sentbyte=1200,
        rcvddelta=12000,
        sentdelta=1200,
        sessionid=1001,
        app=app,
        appcat=appcat,
    )
    e.traffic(
        tr(d, 9, 4),
        PC1,
        50001,
        VIDEO,
        interim=True,
        duration=240,
        durationdelta=120,
        rcvdbyte=18000,
        sentbyte=1800,
        rcvddelta=6000,
        sentdelta=600,
        sessionid=1001,
        app=app,
        appcat=appcat,
    )
    e.traffic(
        tr(d, 9, 5),
        PC1,
        50001,
        VIDEO,
        duration=300,
        rcvdbyte=18600,
        sentbyte=1860,
        action="close",
        sessionid=1001,
        app=app,
        appcat=appcat,
    )
    # S2: 09:02-09:03 to an address a DNS answer of 09:01:30 named (no web log).
    e.dns(tr(d, 9, 1, 30), PC1, "mail.posta.example", [MAIL])
    e.traffic(
        tr(d, 9, 3),
        PC1,
        50002,
        MAIL,
        sessionid=1002,
        duration=60,
        rcvdbyte=3000,
        sentbyte=300,
        action="close",
    )
    # Background: S3 Windows Update (pattern), S4 below 2 KB, S5 DNS port, S6 category Update.
    e.web(tr(d, 9, 58), PC1, 1003, "download.windowsupdate.com", "203.0.113.40")
    e.traffic(
        tr(d, 10),
        PC1,
        50003,
        "203.0.113.40",
        sessionid=1003,
        duration=120,
        rcvdbyte=600000,
        sentbyte=6000,
        action="close",
    )
    e.web(tr(d, 10, 30), PC1, 1004, "tracker.reklam.example", "203.0.113.50")
    e.traffic(
        tr(d, 10, 30),
        PC1,
        50004,
        "203.0.113.50",
        sessionid=1004,
        duration=0,
        rcvdbyte=900,
        sentbyte=300,
        action="close",
    )
    e.traffic(
        tr(d, 10, 31),
        PC1,
        50005,
        "8.8.8.8",
        53,
        sessionid=1005,
        duration=1,
        proto=17,
        rcvdbyte=5000,
        sentbyte=100,
        action="accept",
    )
    e.traffic(
        tr(d, 11),
        PC1,
        50006,
        "203.0.113.60",
        sessionid=1006,
        duration=60,
        rcvdbyte=50000,
        sentbyte=1000,
        app="MS.Windows.Update",
        appcat="Update",
        dstinetsvc="Microsoft-Azure",
        action="close",
    )
    # Blocked attempts: web filter, DNS filter, application control.
    e.web(
        tr(d, 11, 15),
        PC1,
        1007,
        "bahis.example",
        "203.0.113.70",
        url="/canli",
        cat=991,
        catdesc="Kumar",
        action="blocked",
    )
    e.dns(tr(d, 11, 15, 1), PC1, "bahis.example", [], cat=991, catdesc="Kumar", action="block")
    e.app(
        tr(d, 11, 20),
        PC1,
        1008,
        None,
        "203.0.113.80",
        app="BitTorrent",
        appcat="P2P",
        action="block",
    )
    # S7: an evening session (off hours).
    e.web(
        tr(d, 19, 30),
        PC1,
        1009,
        "haber.example",
        NEWS,
        url="/gundem",
        cat=904,
        catdesc="Haber",
        action="passthrough",
    )
    e.traffic(
        tr(d, 19, 32),
        PC1,
        50009,
        NEWS,
        sessionid=1009,
        duration=120,
        rcvdbyte=10000,
        sentbyte=2000,
        action="close",
    )

    # -- PC-02 (nobody) -------------------------------------------------------------------------
    # S13: a lost interim log — the final remainder covers it.
    e.web(
        tr(d, 15),
        PC2,
        2013,
        "bulut.example",
        "203.0.113.90",
        cat=907,
        catdesc="Bulut Depolama",
        action="passthrough",
    )
    e.traffic(
        tr(d, 15, 2),
        PC2,
        51013,
        "203.0.113.90",
        sessionid=2013,
        interim=True,
        duration=120,
        durationdelta=120,
        rcvdbyte=4000,
        sentbyte=0,
        rcvddelta=4000,
        sentdelta=0,
    )
    e.traffic(
        tr(d, 15, 6),
        PC2,
        51013,
        "203.0.113.90",
        sessionid=2013,
        duration=360,
        rcvdbyte=16000,
        sentbyte=0,
        action="close",
    )
    # S8: 23:59 → 00:01 of the next day.
    e.web(
        tr(d, 23, 59),
        PC2,
        2008,
        "www.muzik.example",
        "203.0.113.95",
        cat=906,
        catdesc="Müzik",
        action="passthrough",
    )
    e.traffic(
        tr(following, 0, 1),
        PC2,
        51008,
        "203.0.113.95",
        sessionid=2008,
        duration=120,
        rcvdbyte=6000,
        sentbyte=0,
        action="close",
    )

    # -- PC-03 (Burak until 12:00, then Can) ----------------------------------------------------
    e.web(
        tr(d, 11),
        PC3,
        3009,
        "portal.example",
        "203.0.113.100",
        cat=901,
        catdesc="İş Uygulamaları",
        action="passthrough",
    )
    e.traffic(
        tr(d, 11, 2),
        PC3,
        52009,
        "203.0.113.100",
        sessionid=3009,
        duration=120,
        rcvdbyte=12000,
        sentbyte=0,
        action="close",
    )
    e.web(tr(d, 11, 59), PC3, 3010, "portal.example", "203.0.113.100")
    e.traffic(
        tr(d, 12, 1),
        PC3,
        52010,
        "203.0.113.100",
        sessionid=3010,
        duration=120,
        rcvdbyte=8000,
        sentbyte=0,
        action="close",
    )
    e.web(tr(d, 13), PC3, 3011, "portal.example", "203.0.113.100")
    e.traffic(
        tr(d, 13, 1),
        PC3,
        52011,
        "203.0.113.100",
        sessionid=3011,
        duration=60,
        rcvdbyte=3000,
        sentbyte=0,
        action="close",
    )

    # -- 172.20.41.77 (no lease) --------------------------------------------------------------
    e.traffic(
        tr(d, 14, 1),
        BARE,
        53012,
        "203.0.113.110",
        sessionid=4012,
        duration=60,
        rcvdbyte=5000,
        sentbyte=0,
        action="close",
    )
    session.commit()

    rebuild_days(session, d, following, now=tr(following + timedelta(days=1), 9))
    ayse = _person(session, "Ayşe Yılmaz", "Finans", "Muhasebe Uzmanı")
    burak = _person(session, "Burak Demir", "Planlama", "Planlama Uzmanı")
    can = _person(session, "Can Öztürk", "Finans", "Mali Analist")
    for mac, name in ((PC1_MAC, "Ayşe'nin bilgisayarı"), (PC3_MAC, "Planlama bilgisayarı")):
        device = session.get_one(Device, mac)
        device.name = name
        device.type = "pc"
    session.flush()
    _assign(session, PC1_MAC, ayse, tr(date(2026, 10, 1), 9))
    _assign(session, PC3_MAC, burak, tr(date(2026, 10, 1), 9))
    _assign(session, PC3_MAC, can, tr(d, 12))
    session.commit()
    return GoldenSite(ayse=ayse, burak=burak, can=can)
