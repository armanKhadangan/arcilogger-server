"""The raw-line check of the official report (``reports.consistency``): without the database,
lines that are no spool lines or of another table, tables the check does not cover, IP and MAC
spellings, the line that ended a ``superseded`` lease (another device took the address, or the
lease's MAC took another one, D33, also across the two DHCP tables) and the session record of a
srcmac chain; with the database (contract 3.5), the golden FortiGate site, a FortiGate lease
changed after ingest and a D33-closed lease cited in a report."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import PC, PC_MAC, PHONE_MAC, build_site, iso
from arcilogger_api.pdftext import normalized, pdf_text
from arcilogger_api.reports.consistency import Claim, check_line, outcome_claims, ref_key
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import to_site
from arcilogger_db.models import Lease, Source
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import (
    DeviceInfo,
    EvidenceRef,
    Explanation,
    LeaseInfo,
    LookupOutcome,
    LookupRequest,
    RawRef,
)
from arcilogger_worker.lookup.refs import dhcp_ref, fg_dhcp_ref
from arcilogger_worker.lookup.types import AttributionChain
from arcilogger_worker.testing import FIREWALL, ROUTER, Evidence, tr

from .test_report_pdf import body, download, report_row, signed_in

DAY = date(2026, 10, 12)
#: The example chain's lease (10:42:18 site time).
AT = datetime(2026, 10, 12, 7, 42, 18, tzinfo=UTC)
DHCP = b"<30>2026-10-12T10:42:18.000+03:00 CORE-R1 dhcp,info dhcp42 assigned 172.20.42.57 for "
#: D33: a client of 172.20.42.57 (MAC :31) takes 172.20.42.58 four seconds later.
D33_FIRST, D33_SECOND = tr(DAY, 8, 41, 10), tr(DAY, 8, 41, 14)
MAC_31, MAC_32 = "00:00:5e:00:53:31", "00:00:5e:00:53:32"
PC_58 = "172.20.42.58"

Login = Callable[..., LoggedIn]


def _ref(table: str, *, time: datetime = AT) -> EvidenceRef:
    return EvidenceRef(table=table, row_id=7, time=time, raw=RawRef("core-r1", DAY, 3, 0))


def _line(message: bytes) -> bytes:
    return format_line(AT, "172.20.10.1", message).rstrip(b"\n")


def _fortigate(time: datetime, message: str) -> bytes:
    return format_line(time, FIREWALL, message.encode()).rstrip(b"\n")


def _ack_message(time: datetime, ip: str, mac: str, interface: str = "vlan42") -> str:
    """A FortiGate DHCP ACK in the format of ``Evidence.fortigate_dhcp`` (lease 86400)."""
    local = to_site(time)
    return (
        f'<190>date={local:%Y-%m-%d} time={local:%H:%M:%S} devname="FIREWALL-01" '
        f'devid="FGT0000000000000" eventtime={int(time.timestamp() * 1e9)} tz="+0300" '
        'logid="0100026001" type="event" subtype="system" level="information" vd="root" '
        f'logdesc="DHCP Ack log" interface="{interface}" dhcp_msg="Ack" mac="{mac.upper()}" '
        f'ip={ip} lease=86400 msg="DHCP server sends a DHCPACK"'
    )


def _ack(
    time: datetime, ip: str, mac: str, line: int, interface: str = "vlan42"
) -> tuple[EvidenceRef, bytes]:
    """An ``fg_dhcp`` ACK: its evidence reference (the summary the lease timeline stores) and
    its spool line."""
    row = SimpleNamespace(
        id=line,
        time=time,
        source="fortigate",
        raw_day=DAY,
        raw_line=line,
        raw_offset=0,
        interface=interface,
        ip=ip,
        mac=mac,
        hostname=None,
        lease=86400,
        logid="0100026001",
    )
    return fg_dhcp_ref(row, "assigned"), _fortigate(time, _ack_message(time, ip, mac, interface))


def _lease(
    ip: str, mac: str, evidence: tuple[EvidenceRef, ...], *, end: datetime | None = None
) -> LeaseInfo:
    return LeaseInfo(
        lease_id=1,
        ip=ip,
        mac=mac,
        vlan=42,
        hostname=None,
        start=evidence[0].time,
        end=end,
        end_reason="superseded" if end is not None else "open",
        confidence="high",
        evidence=evidence,
        source_table=evidence[0].table,
    )


def _chain(lease: LeaseInfo | None = None, **fields: Any) -> AttributionChain:
    values: dict[str, Any] = {
        "internal_ip": lease.ip if lease is not None else PC,
        "at": AT,
        "vlan": 42,
        "vlan_name": None,
        "lease": lease,
        "device": None,
        "person": None,
        "department": None,
        "department_source": "none",
        "confidence": "high",
    }
    return AttributionChain(**{**values, **fields})


def _outcome(chain: AttributionChain) -> LookupOutcome:
    return LookupOutcome(
        request=LookupRequest(at=AT, internal_ip=chain.internal_ip),
        mode="internal",
        matches=(),
        double_nat=False,
        attribution=chain,
        explanations=(),
        data_window=None,
        tolerance_seconds=60,
        generated_at=AT,
    )


def _claims(chain: AttributionChain, ref: EvidenceRef) -> list[Claim]:
    return outcome_claims(_outcome(chain)).get(ref_key(ref), [])


def test_lines_that_cannot_support_the_row() -> None:
    assert check_line(_ref("mt_dhcp"), b"no spool line", 0, ()) == {
        "ok": False,
        "mismatches": [],
        "error": "not_a_raw_line",
    }
    other = _line(b"<30>2026-10-12T10:42:18.000+03:00 CORE-R1 system,info user admin logged in")
    assert check_line(_ref("mt_dhcp"), other, 0, ())["error"] == "other_record_type"
    # a MikroTik line cited as a FortiGate DHCP record is no such record
    assert check_line(_ref("fg_dhcp"), other, 0, ())["error"] == "other_record_type"
    # tables the check does not cover are marked, never counted as a contradiction
    assert check_line(_ref("fg_event"), other, 0, ())["error"] == "not_checked"


def test_fortigate_dhcp_lines_are_checked_and_fg_event_lines_are_not() -> None:
    ref, line = _ack(D33_FIRST, "172.20.42.57", MAC_31, 3)
    claim = Claim({"ip": "172.20.42.57", "mac": MAC_31})
    assert check_line(ref, line, 0, [claim]) == {"ok": True, "mismatches": [], "error": None}
    # the same line cited as an unconverted FortiGate DHCP row (D8): shown, never compared
    unconverted = EvidenceRef("fg_event", 3, D33_FIRST, ref.raw, "FortiGate DHCP kaydı")
    assert check_line(unconverted, line, 0, [claim])["error"] == "not_checked"


def test_spellings_and_the_closing_line_of_a_superseded_lease() -> None:
    line = _line(DHCP + b"00:00:5E:00:53:57 PLN-PC14")
    # the database spells MACs in lower case, RouterOS in upper case
    claim = Claim({"ip": "172.20.42.57", "mac": "00:00:5e:00:53:57"})
    assert check_line(_ref("mt_dhcp"), line, 0, [claim])["ok"] is True
    wrong = check_line(_ref("mt_dhcp"), line, 0, [Claim({"ip": "172.20.42.5x"})])
    assert wrong["mismatches"] == [{"field": "ip", "report": "172.20.42.5x", "raw": "172.20.42.57"}]
    late = check_line(_ref("mt_dhcp", time=AT.replace(second=19)), line, 0, ())
    assert late["mismatches"][0]["field"] == "time"

    # The lease of another MAC ended by this line ("superseded"): only the IP must match (a ref
    # without a summary names no other address).
    lease = LeaseInfo(
        lease_id=1,
        ip="172.20.42.57",
        mac=MAC_32,
        vlan=42,
        hostname=None,
        start=datetime(2026, 10, 12, 5, 0, tzinfo=UTC),
        end=AT,
        end_reason="superseded",
        confidence="high",
        evidence=(_ref("mt_dhcp"),),
    )
    (claims,) = outcome_claims(_outcome(_chain(lease))).values()
    assert [claim.values for claim in claims] == [{"ip": "172.20.42.57"}]
    assert check_line(_ref("mt_dhcp"), line, 0, claims)["ok"] is True


def test_a_lease_closed_by_its_macs_ack_for_another_address_claims_the_mac() -> None:
    """D33: ACK(.57, :31), then ACK(.58, :31) ends the .57 lease; the closing line names .58,
    so it claims only the MAC the lease shares with it."""
    first, first_line = _ack(D33_FIRST, "172.20.42.57", MAC_31, 3)
    closing, closing_line = _ack(D33_SECOND, PC_58, MAC_31, 4)
    chain = _chain(_lease("172.20.42.57", MAC_31, (first, closing), end=D33_SECOND))
    assert [c.values for c in _claims(chain, first)] == [{"ip": "172.20.42.57", "mac": MAC_31}]
    assert [c.values for c in _claims(chain, closing)] == [{"mac": MAC_31}]
    assert check_line(first, first_line, 0, _claims(chain, first))["ok"] is True
    assert check_line(closing, closing_line, 0, _claims(chain, closing))["ok"] is True


def test_a_lease_superseded_by_another_macs_ack_claims_the_ip() -> None:
    first, _line_first = _ack(D33_FIRST, "172.20.42.57", MAC_31, 3)
    closing, closing_line = _ack(D33_SECOND, "172.20.42.57", MAC_32, 4)
    chain = _chain(_lease("172.20.42.57", MAC_31, (first, closing), end=D33_SECOND))
    assert [c.values for c in _claims(chain, closing)] == [{"ip": "172.20.42.57"}]
    assert check_line(closing, closing_line, 0, _claims(chain, closing))["ok"] is True


def test_a_mikrotik_lease_closed_by_a_fortigate_ack_of_its_mac_claims_the_mac() -> None:
    """The MikroTik variant of D33: an ``mt_dhcp`` lease of .57 ended by the FortiGate's ACK of
    the same MAC for .58."""
    row = SimpleNamespace(
        id=3,
        time=AT,
        source="core-r1",
        raw_day=DAY,
        raw_line=3,
        raw_offset=0,
        server="dhcp42",
        action="assigned",
        ip="172.20.42.57",
        mac="00:00:5e:00:53:57",
        hostname="PLN-PC14",
    )
    first = dhcp_ref(row)
    first_line = _line(DHCP + b"00:00:5E:00:53:57 PLN-PC14")
    end = AT + timedelta(minutes=5)
    closing, closing_line = _ack(end, PC_58, "00:00:5e:00:53:57", 4)
    chain = _chain(_lease("172.20.42.57", "00:00:5e:00:53:57", (first, closing), end=end))
    assert [c.values for c in _claims(chain, closing)] == [{"mac": "00:00:5e:00:53:57"}]
    assert check_line(first, first_line, 0, _claims(chain, first))["ok"] is True
    assert check_line(closing, closing_line, 0, _claims(chain, closing))["ok"] is True


def test_an_altered_closing_row_still_mismatches() -> None:
    first, _first_line = _ack(D33_FIRST, "172.20.42.57", MAC_31, 3)
    closing, closing_line = _ack(D33_SECOND, PC_58, MAC_31, 4)
    # the lease's MAC changed in the database: the closing line's MAC claim fails
    changed = _chain(_lease("172.20.42.57", MAC_32, (first, closing), end=D33_SECOND))
    result = check_line(closing, closing_line, 0, _claims(changed, closing))
    assert result["mismatches"] == [{"field": "mac", "report": MAC_32, "raw": MAC_31}]
    # the stored closing summary edited to name the lease's own address: an IP claim, which fails
    edited = EvidenceRef(
        closing.table, closing.row_id, closing.time, closing.raw, closing.summary.replace(PC_58, PC)
    )
    chain = _chain(_lease(PC, MAC_31, (first, edited), end=D33_SECOND))
    result = check_line(edited, closing_line, 0, _claims(chain, edited))
    assert result["mismatches"] == [{"field": "ip", "report": PC, "raw": PC_58}]
    # the raw closing line names another MAC than the lease
    _other, other_line = _ack(D33_SECOND, PC_58, MAC_32, 4)
    chain = _chain(_lease(PC, MAC_31, (first, closing), end=D33_SECOND))
    result = check_line(closing, other_line, 0, _claims(chain, closing))
    assert result["mismatches"] == [{"field": "mac", "report": MAC_31, "raw": MAC_32}]


def test_the_closing_line_of_a_spaced_interface_reads_its_ip() -> None:
    """sahada doğrula: FortiOS interface names are free text; the summary writes a space as "_"
    and the IP is read from the fixed part after it."""
    first, _first_line = _ack(D33_FIRST, PC, MAC_31, 3, interface="port 1")
    closing, closing_line = _ack(D33_SECOND, PC_58, MAC_31, 4, interface="port 1")
    assert closing.summary.startswith("port_1 assigned 172.20.42.58 for ")
    chain = _chain(_lease(PC, MAC_31, (first, closing), end=D33_SECOND))
    assert [c.values for c in _claims(chain, closing)] == [{"mac": MAC_31}]
    assert check_line(closing, closing_line, 0, _claims(chain, closing))["ok"] is True


def _session_line(srcip: str, srcmac: str) -> bytes:
    """A session record of vlan42 with device identification, as ``Evidence.traffic`` writes
    it."""
    local = to_site(AT)
    return _fortigate(
        AT,
        f'<189>date={local:%Y-%m-%d} time={local:%H:%M:%S} devname="FIREWALL-01" '
        f'devid="FGT0000000000000" eventtime={int(AT.timestamp() * 1e9)} tz="+0300" '
        'logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" '
        f'srcip={srcip} srcport=51001 srcintf="vlan42" dstip=203.0.113.10 dstport=443 '
        'sessionid=51001 proto=6 action="accept" trandisp="snat" transip=100.64.12.9 '
        f'transport=51001 duration=10 srcmac="{srcmac}"',
    )


def _srcmac_chain(ref: EvidenceRef, *, ip: str = PC, mac: str = PC_MAC) -> AttributionChain:
    device = DeviceInfo(
        mac=mac,
        name=None,
        hostnames=(),
        vendor=None,
        randomized=False,
        type=None,
        infrastructure=False,
        confidence="low",
        evidence=(ref,),
    )
    only = Explanation("srcmac_only", "kısmi kanıt", "partial evidence", {"interface": "vlan42"})
    return _chain(
        None,
        internal_ip=ip,
        device=device,
        confidence="low",
        explanations=(only,),
        mac_source="srcmac",
    )


def test_srcmac_chain_claims_pass_and_mismatch() -> None:
    ref = EvidenceRef("fg_traffic", 9, AT, RawRef("fortigate", DAY, 9, 0))
    line = _session_line(PC, "00:00:5E:00:53:57")  # as the FortiGate writes it
    chain = _srcmac_chain(ref)
    claims = _claims(chain, ref)
    assert [c.values for c in claims] == [{"srcip": PC, "srcmac": PC_MAC}]
    assert check_line(ref, line, 0, claims) == {"ok": True, "mismatches": [], "error": None}
    other_mac = _claims(_srcmac_chain(ref, mac=MAC_32), ref)
    assert check_line(ref, line, 0, other_mac)["mismatches"] == [
        {"field": "srcmac", "report": MAC_32, "raw": PC_MAC}
    ]
    other_ip = _claims(_srcmac_chain(ref, ip="172.20.42.99"), ref)
    assert check_line(ref, line, 0, other_ip)["mismatches"] == [
        {"field": "srcip", "report": "172.20.42.99", "raw": PC}
    ]
    # the srcmac alternative of a lease chain (D10) is claimed too
    lease_ref, _lease_line = _ack(D33_FIRST, PC, MAC_31, 3)
    mismatch = Explanation("srcmac_mismatch", "srcmac farklı", "srcmac differs")
    conflict = _chain(
        _lease(PC, MAC_31, (lease_ref,)),
        alternatives=(chain,),
        confidence="low",
        explanations=(mismatch,),
    )
    assert [c.values for c in _claims(conflict, ref)] == [{"srcip": PC, "srcmac": PC_MAC}]


# -- with the database ----------------------------------------------------------------------------


def _internal(client: TestClient, **query: Any) -> dict[str, Any]:
    response = client.post("/api/v1/lookup/5651", json=body(**query))
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_the_golden_fortigate_site_agrees_with_its_raw_lines(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout, dhcp="fortigate")
    signed_in(client, login_as, app_session_factory)
    data = _internal(client, internal_ip=PC)
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = report_row(app_session_factory, data["id"]).inputs
    assert inputs["consistency"]["ok"] is True
    assert inputs["consistency"]["checked"] >= 1
    dhcp = [item for item in inputs["evidence"] if item["table"] == "fg_dhcp"]
    assert dhcp
    for item in dhcp:  # no longer not_checked
        assert item["check"] == {"ok": True, "mismatches": [], "error": None}
    assert "uyuşmuyor" not in text


def test_a_fortigate_lease_changed_after_ingest_is_flagged(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(app_session_factory, layout, dhcp="fortigate")
    with app_session_factory() as session:
        session.execute(update(Lease).where(Lease.mac == PC_MAC).values(mac=PHONE_MAC))
        session.commit()
    signed_in(client, login_as, app_session_factory)
    data = _internal(client, internal_ip=PC)
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = report_row(app_session_factory, data["id"]).inputs
    assert inputs["consistency"]["ok"] is False
    flagged = [item for item in inputs["evidence"] if item["table"] == "fg_dhcp"]
    assert any(
        {"field": "mac", "report": PHONE_MAC, "raw": PC_MAC} in item["check"]["mismatches"]
        for item in flagged
    )
    assert "KANITLA UYUŞMAYAN SONUÇ" in text


def _d33_site(factory: sessionmaker[Session], layout: Layout, *, mikrotik: bool = False) -> None:
    """172.20.42.57 leased to :31 at 08:41:10 (by the FortiGate, or ``mikrotik``: by dhcp42 to
    PC_MAC), then the FortiGate's ACK of the same MAC for .58 at 08:41:14 (D33)."""
    with factory() as session:
        session.merge(Source(name="fortigate", type="fortigate", address=FIREWALL))
        session.merge(Source(name="core-r1", type="mikrotik", address=ROUTER))
        evidence = Evidence(session, layout)
        mac = PC_MAC if mikrotik else MAC_31
        if mikrotik:
            evidence.dhcp(D33_FIRST, "assigned", PC, mac, server="dhcp42")
        else:
            evidence.fortigate_dhcp(D33_FIRST, PC, mac)
        evidence.fortigate_dhcp(D33_SECOND, PC_58, mac)
        session.commit()
        rebuild_day(session, DAY, horizon=tr(DAY + timedelta(days=1), 9))
        session.commit()


def test_a_d33_lease_cited_in_a_report_has_no_mismatch(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    _d33_site(app_session_factory, layout)
    signed_in(client, login_as, app_session_factory)
    data = _internal(client, internal_ip=PC, at=iso(tr(DAY, 8, 41, 12)))
    assert data["outcome"]["attribution"]["lease"]["end_reason"] == "superseded"
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = report_row(app_session_factory, data["id"]).inputs
    closing = [i for i in inputs["evidence"] if i["table"] == "fg_dhcp" and PC_58 in i["summary"]]
    assert closing, "the closing ACK is cited with the lease"
    assert closing[0]["check"] == {"ok": True, "mismatches": [], "error": None}
    assert inputs["consistency"]["ok"] is True
    assert "uyuşmuyor" not in text


def test_a_mikrotik_lease_closed_by_a_fortigate_ack_has_no_mismatch(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    _d33_site(app_session_factory, layout, mikrotik=True)
    signed_in(client, login_as, app_session_factory)
    data = _internal(client, internal_ip=PC, at=iso(tr(DAY, 8, 41, 12)))
    lease = data["outcome"]["attribution"]["lease"]
    assert (lease["source_table"], lease["end_reason"]) == ("mt_dhcp", "superseded")
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = report_row(app_session_factory, data["id"]).inputs
    tables = {item["table"] for item in inputs["evidence"]}
    assert {"mt_dhcp", "fg_dhcp"} <= tables
    assert all(item["check"]["ok"] for item in inputs["evidence"])
    assert "uyuşmuyor" not in text
