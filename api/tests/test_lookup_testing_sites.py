"""The test sites of :mod:`arcilogger_api.lookup_testing` build what they claim (contract 2.12,
3.5): the default site stays byte for byte as before its options; ``dhcp="fortigate"`` gives the
example chain from a FortiGate DHCP lease; ``srcmac`` and ``wan2`` add what they say; and each
scenario helper writes evidence the real lookup (``POST /lookup/5651``) reads as its docstring
says."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import (
    AT,
    DAY,
    DEVICE_IDENTIFICATION,
    FORTIGATE_LEASE,
    PC,
    PC_HOSTNAME,
    PC_MAC,
    PERSON_NAME,
    PHONE,
    PHONE_HOSTNAME,
    PHONE_MAC,
    UNKNOWN_SENDER,
    WAN,
    WAN2,
    accept_privacy,
    build_site,
    device_identified_traffic,
    enter_exits,
    iso,
    unconverted_dhcp_line,
    unknown_sender_traffic,
)
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout
from arcilogger_db.egress import CHANGES_KEY, fingerprint, validate_egresses
from arcilogger_db.models import FgDhcp, FgEvent, FgTraffic, MtDhcp, Setting, Source
from arcilogger_worker.testing import Evidence, tr

Login = Callable[..., LoggedIn]
#: SHA-256 of the default site's spool files, recorded before ``build_site`` had its options:
#: the defaults must keep every raw line (and so every raw reference of the samples and PDFs).
#: A deliberate change of ``Evidence``'s line formats changes them too; record them again then.
SPOOL_SHA256 = {
    "core-r1/2026-10-12.log": "c523db914d5e20b34fa4fe7150c4791ec53d81883c03fea2e7f01373ad670d04",
    "fortigate/2026-10-12.log": "b90f9cab49f0a570e2a99abc5b1a71d6b7ba82fa36763dac342ea61ef2aa34db",
}
#: When the exits were entered: before the evidence.
ENTERED = tr(DAY - timedelta(days=30), 9)
#: The exits of the dual-WAN site: line 1 through WAN, line 2 through WAN2 on wan2.
EXITS = {
    "hat-1": {"mode": "isp_nat", "transips": [WAN]},
    "hat-2": {"mode": "isp_nat", "transips": [WAN2], "interfaces": ["wan2"]},
}


def body(**query: Any) -> dict[str, Any]:
    return {
        "request": {
            "authority": "İstanbul Cumhuriyet Başsavcılığı",
            "file_no": "2026/1234 Soruşturma",
            "request_date": "2026-10-12",
            "reason": "Bilgi talebi yazısı",
        },
        "query": {"at": iso(AT), **query},
    }


def run(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], **query: Any
) -> dict[str, Any]:
    """The outcome of a lookup an administrator runs through the API."""
    who = login_as(client, "admin")
    accept_privacy(factory, who.user_id)
    response = client.post("/api/v1/lookup/5651", json=body(**query))
    assert response.status_code == 201, response.text
    outcome: dict[str, Any] = response.json()["outcome"]
    return outcome


def raw_line(layout: Layout, raw: dict[str, Any]) -> bytes:
    """The spool line a raw reference of an outcome points at."""
    data = layout.spool_file(raw["source"], date.fromisoformat(raw["day"])).read_bytes()
    offset = raw["offset"]
    assert data[:offset].count(b"\n") == raw["line"] - 1
    return data[offset : data.index(b"\n", offset) + 1]


def utc(moment: datetime) -> str:
    """A moment as the outcome and the change record hold it (ISO 8601, UTC)."""
    return moment.astimezone(UTC).isoformat()


def keys(outcome: dict[str, Any]) -> list[str]:
    return [item["key"] for item in outcome["explanations"]]


# -- the defaults ---------------------------------------------------------------------------------


def test_the_defaults_write_the_same_lines_and_rows_as_before(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    site = build_site(app_session_factory, layout)
    spool = {
        path.relative_to(layout.spool).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(layout.spool.rglob("*.log"))
    }
    assert spool == SPOOL_SHA256
    fortigate = layout.spool_file("fortigate", DAY).read_bytes()
    for field in (b"srcintf=", b"dstintf=", b"srcmac=", WAN2.encode()):
        assert field not in fortigate
    assert site.wan2 is None
    with app_session_factory() as session:
        dhcp = session.scalars(select(MtDhcp).order_by(MtDhcp.id)).all()
        assert [(r.id, r.ip, r.server) for r in dhcp] == [
            (site.dhcp_pc, PC, "dhcp42"),
            (site.dhcp_phone, PHONE, "dhcp60"),
        ]
        assert session.scalars(select(FgDhcp)).all() == []
        traffic = session.scalars(select(FgTraffic)).all()
        assert len(traffic) == 6
        assert {(r.srcintf, r.dstintf, r.srcmac, r.transip) for r in traffic} == {
            (None, None, None, WAN)
        }


# -- dhcp="fortigate" -----------------------------------------------------------------------------


def test_fortigate_dhcp_writes_acks_and_renewals_instead_of_mikrotik_rows(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    site = build_site(app_session_factory, layout, dhcp="fortigate")
    with app_session_factory() as session:
        acks = session.scalars(select(FgDhcp).order_by(FgDhcp.time)).all()
        assert [
            (r.time, r.ip, r.mac, r.interface, r.hostname, r.lease, r.dhcp_msg) for r in acks
        ] == [
            (tr(DAY, 9, 37, 52), PHONE, PHONE_MAC, "vlan60", PHONE_HOSTNAME, 86400, "Ack"),
            (tr(DAY, 10, 42, 18), PC, PC_MAC, "vlan42", PC_HOSTNAME, 86400, "Ack"),
            (tr(DAY, 21, 37, 52), PHONE, PHONE_MAC, "vlan60", PHONE_HOSTNAME, 86400, "Ack"),
            (tr(DAY, 22, 42, 18), PC, PC_MAC, "vlan42", PC_HOSTNAME, 86400, "Ack"),
        ]
        assert (site.dhcp_phone, site.dhcp_pc) == (acks[0].id, acks[1].id)
        assert session.scalars(select(MtDhcp)).all() == []
        # Both sources rows, as before (the raw-line view checks the names it cites there).
        assert set(session.scalars(select(Source.name))) >= {"fortigate", "core-r1"}
        assert len(session.scalars(select(FgTraffic)).all()) == 6


@pytest.mark.parametrize(
    ("client_ip", "mac", "hostname", "vlan", "query"),
    [
        (PC, PC_MAC, PC_HOSTNAME, 42, {"public_ip": WAN, "public_port": 51001}),
        (PHONE, PHONE_MAC, PHONE_HOSTNAME, 60, {"internal_ip": PHONE}),
    ],
    ids=["pc-exact", "phone-internal"],
)
def test_fortigate_dhcp_gives_the_example_chain_from_an_fg_dhcp_lease(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    client_ip: str,
    mac: str,
    hostname: str,
    vlan: int,
    query: dict[str, Any],
) -> None:
    site = build_site(app_session_factory, layout, dhcp="fortigate")
    enter_exits(app_session_factory, EXITS, at=ENTERED, by="yonetici")
    outcome = run(client, login_as, app_session_factory, **query)
    if "public_ip" in query:
        assert outcome["mode"] == "exact"
        (match,) = outcome["matches"]
        assert match["traffic"]["srcip"] == client_ip
        chain = match["chain"]
    else:
        assert outcome["mode"] == "internal"
        chain = outcome["attribution"]
    lease = chain["lease"]
    assert (lease["ip"], lease["mac"], lease["hostname"], lease["vlan"]) == (
        client_ip,
        mac,
        hostname,
        vlan,
    )
    assert (lease["source_table"], lease["lease_seconds"]) == ("fg_dhcp", FORTIGATE_LEASE)
    # The first ACK, not the renewal 12 h later (after the moment).
    first, acked = (
        (site.dhcp_pc, tr(DAY, 10, 42, 18))
        if client_ip == PC
        else (site.dhcp_phone, tr(DAY, 9, 37, 52))
    )
    assert lease["last_dhcp_evidence"] == utc(acked)
    cited = [ref for ref in lease["evidence"] if ref["table"] == "fg_dhcp"]
    assert cited[0]["row_id"] == first
    line = raw_line(layout, cited[0]["raw"])
    assert b'logid="0100026001"' in line
    assert mac.upper().encode() in line
    assert not [ref for ref in lease["evidence"] if ref["table"] == "mt_dhcp"]
    if client_ip == PC:
        assert (chain["person"]["name"], chain["department"]) == (PERSON_NAME, "Planlama")


# -- srcmac, wan2 ---------------------------------------------------------------------------------


def test_srcmac_puts_each_clients_interface_and_mac_on_its_sessions(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    site = build_site(app_session_factory, layout, srcmac=True)
    with app_session_factory() as session:
        rows = session.scalars(select(FgTraffic).order_by(FgTraffic.id)).all()
        assert len(rows) == 6
        for row in rows:
            expected = ("vlan42", PC_MAC) if row.srcip == PC else ("vlan60", PHONE_MAC)
            assert (row.srcintf, row.srcmac) == expected
            data = layout.spool_file("fortigate", row.raw_day).read_bytes()
            text = data[row.raw_offset : data.index(b"\n", row.raw_offset)]
            assert f'srcintf="{expected[0]}"'.encode() in text
            assert f'srcmac="{expected[1]}"'.encode() in text
        assert {row.id for row in rows if row.srcip == PC} >= {site.s1, site.s3}
        assert len(session.scalars(select(MtDhcp)).all()) == 2


def test_wan2_adds_one_phone_session_through_the_second_exit(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    site = build_site(app_session_factory, layout, srcmac=True, wan2=True)
    assert site.wan2 is not None
    with app_session_factory() as session:
        row = session.scalars(select(FgTraffic).where(FgTraffic.id == site.wan2)).one()
        assert (row.time, row.srcip, row.srcport, row.dstip, row.dstport) == (
            tr(DAY, 11, 23, 50),
            PHONE,
            51007,
            "203.0.113.10",
            443,
        )
        assert (row.transip, row.transport, row.dstintf) == (WAN2, 51007, "wan2")
        assert (row.srcintf, row.srcmac) == ("vlan60", PHONE_MAC)
        assert len(session.scalars(select(FgTraffic)).all()) == 7
    enter_exits(app_session_factory, EXITS, at=ENTERED, by="yonetici")
    outcome = run(client, login_as, app_session_factory, public_ip=WAN2, public_port=51007)
    assert outcome["mode"] == "exact"
    (match,) = outcome["matches"]
    assert (match["egress"], match["traffic"]["srcip"], match["traffic"]["dstintf"]) == (
        "hat-2",
        PHONE,
        "wan2",
    )
    assert match["chain"]["lease"]["mac"] == PHONE_MAC


# -- the scenario helpers -------------------------------------------------------------------------


def test_unconverted_dhcp_line_is_cited_when_no_chain_is_built(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout, dhcp="fortigate")
    stranger, stranger_mac = "172.20.42.58", "00:00:5e:00:53:58"
    acked = tr(DAY, 10)
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        row = unconverted_dhcp_line(evidence, acked, stranger, stranger_mac, hostname="PLN-PC15")
        session.commit()
        stored = session.scalars(select(FgEvent).where(FgEvent.id == row)).one()
        assert (stored.logid, stored.subtype, stored.logdesc) == (
            "0100026001",
            "system",
            "DHCP Ack log",
        )
        assert stored.extra == {
            "devid": "FGT0000000000000",
            "vd": "root",
            "interface": "vlan42",
            "dhcp_msg": "Ack",
            "mac": stranger_mac.upper(),
            "ip": stranger,
            "lease": "86400",
            "hostname": "PLN-PC15",
        }
        assert session.scalars(select(FgDhcp).where(FgDhcp.ip == stranger)).all() == []
    outcome = run(client, login_as, app_session_factory, internal_ip=stranger)
    assert (outcome["mode"], outcome["attribution"]) == ("none", None)
    assert "fortigate_dhcp_not_converted" in keys(outcome)
    (ref,) = outcome["unconverted_dhcp"]
    assert (ref["table"], ref["row_id"], ref["time"]) == ("fg_event", row, utc(acked))
    line = raw_line(layout, ref["raw"])
    assert b'logid="0100026001"' in line
    assert stranger_mac.upper().encode() in line


def test_device_identified_traffic_keeps_the_identification_fields_in_the_raw_line(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout)
    written = "00:00:5E:00:53:57"  # as the line writes it; the row keeps it normalised
    with app_session_factory() as session:
        row = device_identified_traffic(
            Evidence(session, layout), AT + timedelta(seconds=4), PC, 51010, srcmac=written
        )
        session.commit()
        stored = session.scalars(select(FgTraffic).where(FgTraffic.id == row)).one()
        assert (stored.srcintf, stored.srcmac, stored.extra) == (
            "vlan42",
            PC_MAC,
            dict(DEVICE_IDENTIFICATION),
        )
    enter_exits(app_session_factory, EXITS, at=ENTERED, by="yonetici")
    outcome = run(client, login_as, app_session_factory, public_ip=WAN, public_port=51010)
    (match,) = outcome["matches"]
    traffic = match["traffic"]
    assert (traffic["srcport"], traffic["srcintf"]) == (51010, "vlan42")
    assert traffic["srcmac"] is None  # lookup.srcmac_interfaces is empty
    (ref,) = traffic["evidence"]
    line = raw_line(layout, ref["raw"])
    assert f'srcmac="{written}"'.encode() in line
    for key, value in DEVICE_IDENTIFICATION.items():
        assert f'{key}="{value}"'.encode() in line


def test_unknown_sender_traffic_is_marked_on_its_match(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout)
    name = f"unknown-{UNKNOWN_SENDER}"
    with app_session_factory() as session:
        row = unknown_sender_traffic(
            Evidence(session, layout), AT + timedelta(seconds=2), "172.20.42.61", 51011
        )
        session.commit()
        stored = session.scalars(select(FgTraffic).where(FgTraffic.id == row)).one()
        assert (stored.source, str(stored.sender), stored.transip) == (name, UNKNOWN_SENDER, WAN)
        source = session.get_one(Source, name)
        assert (source.type, source.unknown) == ("fortigate", True)
    enter_exits(app_session_factory, EXITS, at=ENTERED, by="yonetici")
    outcome = run(client, login_as, app_session_factory, public_ip=WAN, public_port=51011)
    (match,) = outcome["matches"]
    assert match["traffic"]["srcip"] == "172.20.42.61"
    assert match["untrusted_sources"] == [name]
    (ref,) = match["traffic"]["evidence"]
    assert ref["raw"]["source"] == name
    assert b"srcip=172.20.42.61 srcport=51011" in raw_line(layout, ref["raw"])


def test_enter_exits_keeps_the_change_record_as_the_settings_do(
    app_session_factory: sessionmaker[Session],
) -> None:
    later = tr(DAY + timedelta(days=2), 14)
    stored = enter_exits(app_session_factory, EXITS, at=ENTERED, by="yonetici")
    assert stored == validate_egresses(EXITS)
    entered = {
        name: {
            "fingerprint": fingerprint(exit_),
            "changed_at": utc(ENTERED),
            "changed_by": "yonetici",
            "deleted": False,
        }
        for name, exit_ in stored.items()
    }
    with app_session_factory() as session:
        egresses = session.get_one(Setting, "nat.egresses")
        changes = session.get_one(Setting, CHANGES_KEY)
        assert (egresses.value, egresses.updated_by, egresses.updated_at) == (
            stored,
            "yonetici",
            ENTERED,
        )
        assert (changes.value, changes.updated_by, changes.updated_at) == (
            entered,
            ":system",
            ENTERED,
        )
    # Later: hat-2 changes, hat-1 stays, hat-3 is new; then hat-3 is removed again.
    changed = {**EXITS, "hat-2": {**EXITS["hat-2"], "transips": ["100.64.13.10"]}}
    enter_exits(
        app_session_factory,
        {**changed, "hat-3": {"mode": "direct", "public": ["203.0.113.9"]}},
        at=later,
        by="ayse",
    )
    enter_exits(app_session_factory, changed, at=later + timedelta(hours=1), by="ayse")
    with app_session_factory() as session:
        record = session.get_one(Setting, CHANGES_KEY).value
    assert record["hat-1"] == entered["hat-1"]
    assert record["hat-2"] == {
        "fingerprint": fingerprint(validate_egresses(changed)["hat-2"]),
        "changed_at": utc(later),
        "changed_by": "ayse",
        "deleted": False,
    }
    assert record["hat-3"]["changed_at"] == utc(later + timedelta(hours=1))
    assert record["hat-3"]["deleted"] is True


def test_enter_exits_without_changes_writes_the_value_alone(
    app_session_factory: sessionmaker[Session],
) -> None:
    enter_exits(app_session_factory, EXITS, at=ENTERED, by="capture", changes=False)
    with app_session_factory() as session:
        egresses = session.get_one(Setting, "nat.egresses")
        assert (egresses.updated_by, egresses.updated_at) == ("capture", ENTERED)
        assert session.get(Setting, CHANGES_KEY) is None
