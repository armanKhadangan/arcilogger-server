"""FortiGate DHCP leases, srcmac and device identification in the official 5651 report (contract
2.12, 3.5): the FortiGate-only report (table names, lease time and last DHCP evidence, the
record-format note taken from the stored outcome), the notes of a ``low`` lease beside its lease
row (no renewal, a renewal after the moment, superseded early, a rollback window, a migration
loaded before the upgrade, a DECLINE outside every network), a source clock off at the moment
in the method section, the unconverted FortiGate DHCP row cited in internal mode only, a chain
without a lease, the srcmac step and the srcmac conflict, and raw lines that keep the device
identification fields."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import (
    AT,
    DAY,
    PC,
    PC_HOSTNAME,
    PC_MAC,
    WAN,
    build_site,
    device_identified_traffic,
    iso,
    unconverted_dhcp_line,
)
from arcilogger_api.pdftext import normalized, pdf_text
from arcilogger_api.reports import render, texts
from arcilogger_api.reports.render import format_notes
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout
from arcilogger_db.models import FgDhcp, FgTraffic, Message, Source, Vlan
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import Explanation, LookupOutcome, LookupRequest
from arcilogger_worker.lookup.texts import explain
from arcilogger_worker.lookup.types import AttributionChain
from arcilogger_worker.testing import FIREWALL, ROUTER, Evidence, tr

from .test_report_pdf import _minimal_inputs, download, lookup, report_row, setting, signed_in

Login = Callable[..., LoggedIn]
#: The lease job's horizon for every day these tests build.
HORIZON = tr(DAY + timedelta(days=9), 9)
T0 = tr(DAY, 8)
DECLINER, OTHER = "00:00:5e:00:53:31", "00:00:5e:00:53:77"
WEEK = 604800
UNVERIFIED = (
    "FortiGate DHCP kayıt biçimi bu kurulumda sahada doğrulanmadı; kira kayıtları FortiOS "
    "belgelerindeki biçime göre okundu."
)


def _site(
    factory: sessionmaker[Session],
    layout: Layout,
    write: Callable[[Evidence], Any],
    first: date = DAY,
    last: date = DAY,
) -> None:
    """The two sources, the evidence ``write`` adds, the leases of ``first`` … ``last`` built
    one day at a time as the nightly job does."""
    with factory() as session:
        session.merge(Source(name="fortigate", type="fortigate", address=FIREWALL))
        session.merge(Source(name="core-r1", type="mikrotik", address=ROUTER))
        write(Evidence(session, layout))
        session.commit()
        day = first
        while day <= last:
            rebuild_day(session, day, horizon=HORIZON)
            session.commit()
            day += timedelta(days=1)


def _report(client: TestClient, **query: Any) -> tuple[dict[str, Any], str]:
    data = lookup(client, **query)
    text = normalized(pdf_text(download(client, data["id"])))
    lowered = text.lower()
    assert "legally compliant" not in lowered
    assert "uyumludur" not in lowered
    return data, text


def _lease_row(text: str) -> str:
    """The first lease row of the report and what stands beside it, up to the device row."""
    return text.split("IP-MAC kaydı / Lease", 1)[1].split("Cihaz / Device", 1)[0]


# -- the FortiGate-only report --------------------------------------------------------------------


def test_fortigate_only_report(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout, dhcp="fortigate")
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, public_ip="192.0.2.44")
    assert len(data["outcome"]["matches"]) == 3  # three chains, one format note
    # the table names: FortiGate DHCP, never MikroTik for it (method item (2) names both kinds)
    assert "FortiGate DHCP kaydı / FortiGate DHCP record (fg_dhcp #" in text
    assert "MikroTik DHCP kaydı" not in text
    assert "MikroTik ARP-LOG kaydı" not in text
    assert "(MikroTik DHCP, FortiGate DHCP olayları) ve saatlik ARP-LOG kayıtlarından kurulan " in (
        text
    )
    # the lease row: source, lease time and the lease's own last DHCP record before the moment
    assert (
        "kaynak: FortiGate DHCP kaydı · kira süresi (FortiGate kaydı): 86400 sn · son DHCP "
        "kanıtı: 12.10.2026 10:42:18" in text
    )
    assert (
        "source: FortiGate DHCP record · lease time (FortiGate record): 86400 s · last DHCP "
        "evidence: 12.10.2026 10:42:18" in text
    )
    assert "son DHCP kanıtı: 12.10.2026 09:37:52" in text  # the phone
    assert "22:42:18" not in _lease_row(text)  # the renewal after the moment
    # the record format: once, in the method section, from the stored outcome
    assert text.count(UNVERIFIED) == 1
    assert UNVERIFIED in text.split("5. Sonuçlar", 1)[0]
    assert "henüz sahada doğrulanmadı (DEVICE-SETUP.md" not in text  # never per chain
    assert "doğrulandı olarak işaretlendi" not in text


def test_the_verified_fortigate_format_names_who_and_when(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout, dhcp="fortigate")
    setting(app_session_factory, "dhcp.fortigate_format_verified", True)  # by "admin"
    signed_in(client, login_as, app_session_factory)
    _data, text = _report(client, public_ip="192.0.2.44")
    marked = re.findall(
        r"FortiGate DHCP kayıt biçimi bu kurulumda \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2} tarihinde "
        r"admin tarafından sahada doğrulandı olarak işaretlendi",
        text,
    )
    assert len(marked) == 1
    assert "sahada doğrulanmadı" not in text


def test_the_format_note_comes_from_the_stored_outcome_not_the_setting(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """A lookup made while unverified, then the setting switched on, then the first download
    (which renders the report): the PDF still says "sahada doğrulanmadı"."""
    build_site(app_session_factory, layout, dhcp="fortigate")
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)
    setting(app_session_factory, "dhcp.fortigate_format_verified", True)
    text = normalized(pdf_text(download(client, data["id"])))
    assert UNVERIFIED in text
    assert "doğrulandı olarak işaretlendi" not in text


def test_the_unverified_format_note_wins_over_a_verified_one() -> None:
    """Should both format keys ever reach one outcome (a chain and its alternative here), the
    note is the unverified sentence alone, the safe side (deviation d-13)."""
    verified = explain("fortigate_format_verified", at="01.10.2026 09:00:00", by="admin")
    alternative = AttributionChain(
        internal_ip=PC,
        at=AT,
        vlan=42,
        vlan_name=None,
        lease=None,
        device=None,
        person=None,
        department=None,
        department_source="none",
        confidence="low",
        explanations=(explain("fortigate_format_unverified"),),
    )
    chain = replace(
        alternative,
        explanations=(verified, Explanation("ip_conflict", "IP çakışması", "IP conflict")),
        alternatives=(alternative,),
    )
    outcome = LookupOutcome(
        request=LookupRequest(at=AT, internal_ip=PC),
        mode="internal",
        matches=(),
        double_nat=False,
        attribution=chain,
        explanations=(),
        data_window=None,
        tolerance_seconds=60,
        generated_at=AT,
    )
    assert format_notes(outcome) == [texts.FORMAT_UNVERIFIED]
    inputs = _minimal_inputs()
    inputs["outcome"] = outcome.to_dict()
    text = normalized(pdf_text(render(inputs).pdf))
    assert text.count(UNVERIFIED) == 1
    assert "doğrulandı olarak işaretlendi" not in text
    # alone, the verified text is printed as stored
    alone = replace(outcome, attribution=replace(alternative, explanations=(verified,)))
    assert format_notes(alone) == [(verified.text_tr, verified.text_en)]


def test_a_mikrotik_report_has_no_fortigate_lease_additions(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout)
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, internal_ip=PC)
    lease = data["outcome"]["attribution"]["lease"]
    # an mt_dhcp chain carries its last DHCP evidence too; only an fg_dhcp row prints it
    assert lease["source_table"] == "mt_dhcp"
    assert lease["last_dhcp_evidence"] is not None
    assert "MikroTik DHCP kaydı (mt_dhcp #" in text
    assert "kira süresi (FortiGate kaydı)" not in text
    assert "son DHCP kanıtı" not in text
    assert "last DHCP evidence" not in text
    assert "kaynak: " not in _lease_row(text)
    assert "sahada doğrula" not in text
    assert "Kira notu / Lease note" not in text


# -- a low lease: its reason beside the lease row -------------------------------------------------


def test_low_lease_notes_beside_the_lease_row(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """No renewal: the ACK at 08:00 (lease 86400), looked up at 23:00 (past half the lease)."""
    _site(
        app_session_factory,
        layout,
        lambda e: e.fortigate_dhcp(T0, PC, PC_MAC, hostname=PC_HOSTNAME),
        DAY,
        DAY + timedelta(days=1),
    )
    signed_in(client, login_as, app_session_factory)
    _data, text = _report(client, internal_ip=PC, at=iso(T0 + timedelta(hours=15)))
    row = _lease_row(text)
    assert re.search(
        r"güven: düşük · kayıt no \d+ · kanıt: K\d+ · kaynak: FortiGate DHCP kaydı · kira süresi "
        r"\(FortiGate kaydı\): 86400 sn · son DHCP kanıtı: 12\.10\.2026 08:00:00 ",
        row,
    )
    assert "Kira notu / Lease note Bu kiranın istenen andan önceki son DHCP kaydı" in row
    assert text.count("Bu kiranın istenen andan önceki son DHCP kaydı") == 1  # not a bullet too
    assert "The last DHCP record of this lease before the requested moment" in row


def test_a_renewal_after_the_moment_is_not_the_last_dhcp_evidence(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """The renewed-after-the-moment golden of 3.4: the same pair's ACK at t0 + 23 h 50 min is
    after the moment t0 + 14 h, so the lease row prints t0."""

    def write(evidence: Evidence) -> None:
        evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=PC_HOSTNAME)
        evidence.traffic(T0 + timedelta(hours=14), PC, 50001)
        evidence.fortigate_dhcp(T0 + timedelta(hours=23, minutes=50), PC, PC_MAC)

    _site(app_session_factory, layout, write, DAY, DAY + timedelta(days=1))
    signed_in(client, login_as, app_session_factory)
    _data, text = _report(client, internal_ip=PC, at=iso(T0 + timedelta(hours=14)))
    row = _lease_row(text)
    assert "son DHCP kanıtı: 12.10.2026 08:00:00" in row
    assert "13.10.2026 07:50:00" not in row
    assert "güven: düşük" in row


def test_a_lease_superseded_early_prints_its_own_last_ack(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """The superseded-early golden of 3.4: A's ACK of .142 at t0 (lease 604800), B's ACK of
    the address at t0 + 5 d, the moment t0 + 4 d: the holder's own ACK time, never B's."""
    ip = "172.20.42.142"

    def write(evidence: Evidence) -> None:
        evidence.fortigate_dhcp(T0, ip, DECLINER, lease=WEEK)
        evidence.fortigate_dhcp(T0 + timedelta(days=5), ip, OTHER, lease=WEEK)

    _site(app_session_factory, layout, write, DAY, DAY + timedelta(days=5))
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, internal_ip=ip, at=iso(T0 + timedelta(days=4)))
    assert data["outcome"]["attribution"]["lease"]["mac"] == DECLINER
    row = _lease_row(text)
    assert "kira süresi (FortiGate kaydı): 604800 sn · son DHCP kanıtı: 12.10.2026 08:00:00" in row
    assert "son DHCP kanıtı: 17.10.2026" not in row  # B's ACK only ended the interval
    assert (
        "12.10.2026 08:00:00 - 17.10.2026 08:00:00 · bitiş: sonraki bir DHCP atamasıyla kapandı / "
        "closed by a later DHCP assignment · güven:" in row
    )
    assert "IP başka bir MAC adresine verildi" not in text
    assert "Kira notu / Lease note Bu kiranın istenen andan önceki son DHCP kaydı" in row


def test_a_line_loaded_in_a_rollback_window_lowers_the_lease_and_is_not_shown(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """:31's ACK at t0 (lease 604800), then an ACK of .57 to :44 that the previous version
    loaded as an ``fg_event`` row at t0 + 26 h: the lease is low with its reason beside the
    lease row, and neither that line nor :44 appears in the report."""
    laptop = "00:00:5e:00:53:44"

    def write(evidence: Evidence) -> None:
        evidence.fortigate_dhcp(T0, PC, DECLINER, lease=WEEK)
        unconverted_dhcp_line(
            evidence, T0 + timedelta(hours=26), PC, laptop, hostname="PLN-NB44", lease=WEEK
        )

    _site(app_session_factory, layout, write, DAY, DAY + timedelta(days=1))
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, internal_ip=PC, at=iso(T0 + timedelta(hours=27, minutes=30)))
    row = _lease_row(text)
    note = "kira zaman çizelgesinin bu kiraya uygulamadığı FortiGate DHCP kayıtları var"
    assert "Kira notu / Lease note Bu kiranın 12.10.2026 08:00:00 anındaki son DHCP kaydından" in (
        row
    )
    assert note in row
    assert text.count(note) == 1
    evidence = report_row(app_session_factory, data["id"]).inputs["evidence"]
    assert "fg_event" not in {item["table"] for item in evidence}
    for hidden in (laptop, laptop.upper(), "PLN-NB44"):
        assert hidden not in text


def test_a_migration_loaded_before_the_upgrade_lowers_the_mikrotik_lease(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """The round-6 golden: the 10-01 09:20 ACK that moved the PC to .120 is an ``fg_event``
    row, a printer on .57 keeps the MikroTik lease open; at 10-04 14:00 the MikroTik chain of
    .57 is low with the post-lease text beside its lease row, and no ``fg_event`` line."""
    move_day = date(2026, 10, 1)
    moved = tr(move_day, 9, 20)

    def write(evidence: Evidence) -> None:
        evidence.dhcp(tr(move_day, 8), "assigned", PC, PC_MAC, server="dhcp42", hostname="PC14")
        unconverted_dhcp_line(evidence, moved, "172.20.42.120", PC_MAC, hostname="IDR-NB07")
        evidence.fortigate_dhcp(tr(move_day + timedelta(days=4), 21, 20), "172.20.42.120", PC_MAC)
        for hour in range(10, 24 * 6, 2):
            evidence.traffic(tr(move_day, 0) + timedelta(hours=hour), PC, 40000 + hour)

    _site(app_session_factory, layout, write, move_day, move_day + timedelta(days=5))
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, internal_ip=PC, at=iso(tr(move_day + timedelta(days=3), 14)))
    lease = data["outcome"]["attribution"]["lease"]
    assert (lease["source_table"], lease["confidence"]) == ("mt_dhcp", "low")
    row = _lease_row(text)
    assert "güven: düşük" in row
    assert "Kira notu / Lease note Bu kiranın 01.10.2026 08:00:00 anındaki son DHCP kaydından" in (
        row
    )
    assert "kira süresi (FortiGate kaydı)" not in row  # a MikroTik lease
    evidence = report_row(app_session_factory, data["id"]).inputs["evidence"]
    assert "fg_event" not in {item["table"] for item in evidence}
    assert "IDR-NB07" not in text


def test_a_decline_outside_every_network_is_low_beside_its_lease_row(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """The DECLINE golden without a network: :31 declines .57 and takes .58; with VLAN 42's
    network removed nothing can close the declined address, so the chain is low."""
    with app_session_factory() as session:
        session.execute(delete(Vlan).where(Vlan.subnet == "172.20.42.0/24"))
        session.commit()

    def write(evidence: Evidence) -> None:
        evidence.fortigate_dhcp(tr(DAY, 8, 41, 10), PC, DECLINER)
        evidence.fortigate_dhcp(tr(DAY, 8, 41, 14), "172.20.42.58", DECLINER)
        evidence.traffic(tr(DAY, 10, 12), PC, 50001)

    _site(app_session_factory, layout, write)
    signed_in(client, login_as, app_session_factory)
    _data, text = _report(client, internal_ip=PC, at=iso(tr(DAY, 10, 12)))
    row = _lease_row(text)
    assert "güven: düşük" in row
    assert "Kira notu / Lease note 172.20.42.57 adresi tanımlı hiçbir ağda değil" in row
    assert text.count("adresi tanımlı hiçbir ağda değil") == 1


# -- FortiGate DHCP rows outside the lease timeline (D8) ------------------------------------------


def test_unconverted_dhcp_is_cited_in_internal_mode_only(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """An internal lookup prints the covering ``fg_event`` line in section 6, marked as no
    attribution, never another client's later DHCP line; a candidate report with five
    chainless candidates names their IPs and cites no ``fg_event`` line."""
    acked = AT - timedelta(days=4)
    other_mac = "00:00:5e:00:53:7a"

    def write(evidence: Evidence) -> None:
        unconverted_dhcp_line(evidence, acked, PC, PC_MAC, hostname=PC_HOSTNAME, lease=WEEK)
        unconverted_dhcp_line(
            evidence, AT + timedelta(hours=1), PC, other_mac, hostname="PLN-NB7A", lease=WEEK
        )
        evidence.traffic(AT, PC, 51001, transip=WAN)

    _site(app_session_factory, layout, write)
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, internal_ip=PC)
    evidence = report_row(app_session_factory, data["id"]).inputs["evidence"]
    (cited,) = [item for item in evidence if item["table"] == "fg_event"]
    assert cited["line"]["status"] == "ok"
    assert cited["check"]["error"] == "not_checked"
    assert 'logid="0100026001"' in cited["line"]["text"]
    assert PC_MAC.upper() in cited["line"]["text"]
    section_6 = text.split("6. Kanıt listesi", 1)[1]
    assert "FortiGate olay kaydı / FortiGate event record (fg_event #" in section_6
    assert cited["line"]["text"] in section_6
    assert (
        "kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez / not in the "
        "lease timeline; no chain built; no confidence given" in section_6
    )
    # its summary row: the fixed label in both languages, not the Turkish-only stored summary
    assert (
        "Özet / Summary FortiGate DHCP kaydı (kira zaman çizelgesine girmedi; zincir kurulmadı, "
        "güven düzeyi verilmez) / FortiGate DHCP record (not in the lease timeline; no chain "
        "built; no confidence given)" in section_6
    )
    for hidden in (other_mac, other_mac.upper(), "PLN-NB7A"):
        assert hidden not in text


def test_chainless_candidates_name_their_ips_and_cite_no_event_line(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    five = [f"172.20.42.{61 + n}" for n in range(5)]

    def write(evidence: Evidence) -> None:
        for n, ip in enumerate(five):
            unconverted_dhcp_line(
                evidence,
                AT - timedelta(days=4),
                ip,
                f"00:00:5e:00:53:{0x61 + n:02x}",
                hostname=f"PLN-NB{n:02}",
                lease=WEEK,
            )
            evidence.traffic(AT + timedelta(seconds=n), ip, 51001 + n, transip=WAN)

    _site(app_session_factory, layout, write)
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, public_ip="198.51.100.20")
    assert len(data["outcome"]["matches"]) == 5
    assert (
        "için o anı kapsayan FortiGate DHCP kayıtları var, ancak kira kanıtına çevrilmediler"
        in (text)
    )
    evidence = report_row(app_session_factory, data["id"]).inputs["evidence"]
    assert "fg_event" not in {item["table"] for item in evidence}
    assert "00:00:5e:00:53:6" not in text.lower()
    assert "PLN-NB0" not in text
    # each match without a chain prints its reason (a missing chain, 2.12)
    assert text.count("Zincir kurulamadı: o anda bu iç IP adresi için kira kaydı yok.") == 5
    assert text.count("No chain: no lease covered this internal IP address at that moment.") == 5


# -- srcmac and device identification -------------------------------------------------------------


def test_srcmac_step_and_conflict(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """A client without a lease on a listed interface: the srcmac step, partial evidence in the
    device-identification wording, checked against its session line; a session of the PC
    whose srcmac is another MAC: both chains, the other under the srcmac header, both low."""
    static, static_mac = "172.20.42.88", "00:00:5e:00:53:88"
    other_mac = "00:00:5e:00:53:99"
    # the site's own sessions carry no srcmac: .57 shows one srcmac only (no srcmac_ambiguous)
    build_site(app_session_factory, layout, dhcp="fortigate")
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        evidence.traffic(AT, static, 51060, srcintf="vlan42", dstintf="wan1", srcmac=static_mac)
        evidence.traffic(
            AT + timedelta(seconds=2), PC, 51061, srcintf="vlan42", dstintf="wan1", srcmac=other_mac
        )
        session.commit()
    setting(app_session_factory, "lookup.srcmac_interfaces", ["vlan42"])
    signed_in(client, login_as, app_session_factory)

    data, text = _report(client, internal_ip=static)
    assert data["outcome"]["attribution"]["mac_source"] == "srcmac"
    assert (
        f"MAC adresi / MAC address MAC {static_mac} · MAC adresi (kısmi kanıt): FortiGate oturum "
        "kaydının srcmac alanı, vlan42 arayüzü; FortiGate'in aygıt tanıma bilgisine göre kaynak IP "
        "adresi o anda bu MAC adresiyle ilişkilendirilmişti; kira kaydı yok" in text
    )
    assert "MAC address (partial evidence): srcmac field of the FortiGate session record" in text
    assert "geldiğini gösterir" not in text
    inputs = report_row(app_session_factory, data["id"]).inputs
    assert inputs["consistency"]["ok"] is True  # srcip and srcmac of the session line agree
    (session_line,) = [item for item in inputs["evidence"] if item["table"] == "fg_traffic"]
    assert session_line["check"] == {"ok": True, "mismatches": [], "error": None}

    data, text = _report(client, public_ip="198.51.100.20", public_port=51061)
    conflicted = [m for m in data["outcome"]["matches"] if m["traffic"]["srcport"] == 51061]
    assert conflicted
    for match in conflicted:  # a srcmac_mismatch conflict whose alternative has no lease (D10)
        assert "srcmac_mismatch" in {item["key"] for item in match["chain"]["explanations"]}
        (other,) = match["chain"]["alternatives"]
        assert (other["mac_source"], other["lease"]) == ("srcmac", None)
    assert f"MAC {PC_MAC}" in text
    assert f"MAC {other_mac}" in text
    alternative = text.split(texts.ALTERNATIVE_SRCMAC, 1)[1]
    # the srcmac alternative without a lease: its MAC row, never a lease row
    assert "IP-MAC kaydı / Lease" not in alternative.split("Zincir güveni / Confidence", 1)[0]
    assert "Olası diğer zincir (srcmac, kısmi kanıt) / Possible other chain (srcmac, partial " in (
        text
    )
    assert f"MAC {other_mac} · MAC adresi (kısmi kanıt)" in alternative
    found = re.search(r"Zincir güveni / Confidence (\S+)", alternative)
    assert found is not None
    assert found.group(1) == "düşük"
    assert "Zincir güveni / Confidence düşük" in text.split("Olası diğer zincir", 1)[0]
    assert "geldiğini gösterir" not in text


def test_raw_lines_keep_the_device_identification_fields(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """D21: cited session lines of an interface with device identification print ``srcmac``
    and the F19 fields as they are, with ``lookup.srcmac_interfaces`` empty and for other
    people's candidate sessions too, while no typed field of the outcome carries the MAC."""
    build_site(app_session_factory, layout)
    shown = "00:00:5E:00:53:5A"  # as the FortiGate writes it, another MAC than the lease's
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        for n in range(3):
            device_identified_traffic(
                evidence, AT + timedelta(seconds=n), PC, 51071 + n, srcmac=shown
            )
        session.commit()
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, public_ip="192.0.2.44")
    identified = [
        m for m in data["outcome"]["matches"] if m["traffic"]["srcport"] in (51071, 51072)
    ]
    assert identified
    assert f'srcmac="{shown}"' in text
    assert 'srcname="PLN-PC14"' in text
    assert 'unauthuser="ornek.kullanici"' in text
    stored = report_row(app_session_factory, data["id"]).inputs["outcome"]
    assert all(m["traffic"]["srcmac"] is None for m in stored["matches"])
    assert shown.lower() not in json.dumps(stored).lower()


# -- the source clock (D28) -----------------------------------------------------------------------


def _as_loaded_behind(session: Session, behind: timedelta) -> None:
    """The FortiGate's typed rows as ingest loads them from a clock ``behind`` its receive
    time, each with its ``messages`` row, and its files' ``search_counts`` (the worker's
    "source clock" golden of 3.4)."""
    for model in (FgTraffic, FgDhcp):
        session.execute(
            update(model)
            .where(model.source == "fortigate")
            .values(time_received=model.time + behind)
        )
    rows: list[tuple[FgTraffic | FgDhcp, str]] = [
        (row, "traffic")
        for row in session.scalars(select(FgTraffic).where(FgTraffic.source == "fortigate"))
    ]
    rows += [
        (row, "event")
        for row in session.scalars(select(FgDhcp).where(FgDhcp.source == "fortigate"))
    ]
    days = set()
    for row, kind in rows:
        session.add(
            Message(
                time=row.time,
                time_received=row.time_received,
                source="fortigate",
                sender=row.sender,
                raw_day=row.raw_day,
                raw_line=row.raw_line,
                raw_offset=row.raw_offset,
                type=kind,
                subtype=row.subtype,
                text="",
            )
        )
        days.add(row.raw_day)
    session.flush()
    days |= set(session.scalars(select(Message.raw_day).where(Message.source == "fortigate")))
    for day in days:
        rebuild_stream(session.connection(), "fortigate", day)
    session.commit()


def test_a_source_clock_offset_is_printed_in_the_method(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """The FortiGate's rows around the moment are stamped 8 minutes before they arrive: the
    method section prints ``source_clock_offset`` with the offset, and the chain is low."""
    behind = timedelta(minutes=8)
    with app_session_factory() as session:
        session.merge(Source(name="fortigate", type="fortigate", address=FIREWALL))
        evidence = Evidence(session, layout)
        evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=PC_HOSTNAME)
        evidence.traffic(AT, PC, 51001, transip=WAN)
        for k in range(20):
            time = AT - timedelta(minutes=50) + timedelta(minutes=5 * k)
            evidence.heartbeat(time, source="fortigate", received=time + behind)
        session.commit()
        _as_loaded_behind(session, behind)
        rebuild_day(session, DAY, horizon=HORIZON)
        session.commit()
    setting(app_session_factory, "nat.egresses", {"wan": {"mode": "isp_nat", "transips": [WAN]}})
    signed_in(client, login_as, app_session_factory)
    data, text = _report(client, public_ip=WAN, public_port=51001, protocol=6)
    assert data["mode"] == "exact"
    method = text.split("4. Yöntem", 1)[1].split("5. Sonuçlar", 1)[0]
    assert "kayıt zamanları ARCILOGGER'ın alma zamanından" in method
    assert "fortigate: -480 (sınır 120 saniye)" in method
    assert "fortigate: -480 (limit 120 seconds)" in method
    assert "güven: düşük" in _lease_row(text)
