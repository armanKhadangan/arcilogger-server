"""The raw-line check of the official report without the database (``reports.consistency``):
lines that are no spool lines or of another table, tables the check does not cover, IP and MAC
spellings, and the closing line of a lease whose IP went to another device."""

from __future__ import annotations

from datetime import UTC, date, datetime

from arcilogger_api.reports.consistency import Claim, check_line, outcome_claims
from arcilogger_core.rawlog import format_line
from arcilogger_worker.lookup import EvidenceRef, LeaseInfo, LookupOutcome, LookupRequest, RawRef
from arcilogger_worker.lookup.types import AttributionChain

DAY = date(2026, 10, 12)
#: The example chain's lease (10:42:18 site time).
AT = datetime(2026, 10, 12, 7, 42, 18, tzinfo=UTC)
DHCP = b"<30>2026-10-12T10:42:18.000+03:00 CORE-R1 dhcp,info dhcp42 assigned 172.20.42.57 for "


def _ref(table: str, *, time: datetime = AT) -> EvidenceRef:
    return EvidenceRef(table=table, row_id=7, time=time, raw=RawRef("core-r1", DAY, 3, 0))


def _line(message: bytes) -> bytes:
    return format_line(AT, "172.20.10.1", message).rstrip(b"\n")


def test_lines_that_cannot_support_the_row() -> None:
    assert check_line(_ref("mt_dhcp"), b"no spool line", 0, ()) == {
        "ok": False,
        "mismatches": [],
        "error": "not_a_raw_line",
    }
    other = _line(b"<30>2026-10-12T10:42:18.000+03:00 CORE-R1 system,info user admin logged in")
    assert check_line(_ref("mt_dhcp"), other, 0, ())["error"] == "other_record_type"
    # tables the check does not cover are marked, never counted as a contradiction
    assert check_line(_ref("fg_event"), other, 0, ())["error"] == "not_checked"


def test_spellings_and_the_closing_line_of_a_superseded_lease() -> None:
    line = _line(DHCP + b"00:00:5E:00:53:57 PLN-PC14")
    # the database spells MACs in lower case, RouterOS in upper case
    claim = Claim({"ip": "172.20.42.57", "mac": "00:00:5e:00:53:57"})
    assert check_line(_ref("mt_dhcp"), line, 0, [claim])["ok"] is True
    wrong = check_line(_ref("mt_dhcp"), line, 0, [Claim({"ip": "172.20.42.5x"})])
    assert wrong["mismatches"] == [{"field": "ip", "report": "172.20.42.5x", "raw": "172.20.42.57"}]
    late = check_line(_ref("mt_dhcp", time=AT.replace(second=19)), line, 0, ())
    assert late["mismatches"][0]["field"] == "time"

    # The lease of another MAC ended by this line ("superseded"): only the IP must match.
    lease = LeaseInfo(
        lease_id=1,
        ip="172.20.42.57",
        mac="f0:1f:af:00:00:01",
        vlan=42,
        hostname=None,
        start=datetime(2026, 10, 12, 5, 0, tzinfo=UTC),
        end=AT,
        end_reason="superseded",
        confidence="high",
        evidence=(_ref("mt_dhcp"),),
    )
    chain = AttributionChain(
        internal_ip=lease.ip,
        at=AT,
        vlan=42,
        vlan_name=None,
        lease=lease,
        device=None,
        person=None,
        department=None,
        department_source="none",
        confidence="high",
    )
    outcome = LookupOutcome(
        request=LookupRequest(at=AT, internal_ip=lease.ip),
        mode="internal",
        matches=(),
        double_nat=False,
        attribution=chain,
        explanations=(),
        data_window=None,
        tolerance_seconds=60,
        generated_at=AT,
    )
    (claims,) = outcome_claims(outcome).values()
    assert [claim.values for claim in claims] == [{"ip": "172.20.42.57"}]
    assert check_line(_ref("mt_dhcp"), line, 0, claims)["ok"] is True
