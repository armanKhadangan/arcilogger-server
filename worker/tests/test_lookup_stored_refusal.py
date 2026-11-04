"""The rules a move recorded with a lease is tested on again when a lookup reads it (issue #127).

:func:`.attribution.stored_refusal` is the Python copy of :func:`.trust.refusal`'s rules 1, 2, 4
and 5 for the fields the lease job keeps of a move (sender, interface, logid, ``dhcp_msg``): for
every ``fg_dhcp`` row that rules 3, 6 and 7 accept (an IPv4 address, the FortiGate's own time, a
usable MAC: what the job records a move from), and for every settings case, both give the same
code, against PostgreSQL (a test database has the installer's C locale, under which ``lower()``
changes ASCII letters only: a non-ASCII message is compared as such).
"""

from __future__ import annotations

import itertools
from collections.abc import Collection
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.models import FgDhcp
from arcilogger_worker.lookup import LookupSettings, trust
from arcilogger_worker.lookup.attribution import stored_refusal
from arcilogger_worker.testing import Evidence, tr

STRANGER = "unknown-192.0.2.7"
#: Each case: the settings and the untrusted senders of the day the lookup runs.
CASES: dict[str, tuple[LookupSettings, Collection[str]]] = {
    "defaults": (LookupSettings(), ()),
    "sender-untrusted": (LookupSettings(), (STRANGER,)),
    "interface-excluded": (LookupSettings(fortigate_interfaces_excluded=("vlan43",)), ()),
    "no-logid-mapped": (LookupSettings(fortigate_logids={}), ()),
    "another-logid-mapped": (
        LookupSettings(fortigate_logids={"0100026003": "assigned", "0100026002": "deassigned"}),
        (),
    ),
    "messages-unrestricted": (LookupSettings(fortigate_dhcp_msgs={}), ()),
    "messages-listed-in-another-case": (
        LookupSettings(fortigate_dhcp_msgs={"0100026001": ("ACK", "DHCPACK"), "0100026002": ()}),
        (),
    ),
    "messages-non-ascii": (LookupSettings(fortigate_dhcp_msgs={"0100026001": ("äck",)}), ()),
    "everything": (
        LookupSettings(
            fortigate_interfaces_excluded=("vlan43", "vlan44"),
            fortigate_logids={"0100026001": "assigned"},
            fortigate_dhcp_msgs={"0100026001": ("Ack",), "0100026003": ("Ack",)},
        ),
        (STRANGER, "fortigate-2"),
    ),
}


@pytest.mark.parametrize("case", list(CASES))
def test_the_stored_refusal_is_the_rows_refusal(
    db_session: Session, evidence: Evidence, case: str
) -> None:
    settings, untrusted = CASES[case]
    values = itertools.product(
        ("fortigate", STRANGER),
        ("vlan42", "vlan43", None),
        ("0100026001", "0100026002", "0100026003"),
        ("Ack", "ack", "DHCPACK", "", "ÄCK"),
    )
    for n, (source, interface, logid, message) in enumerate(values):
        evidence.fortigate_dhcp(
            tr(date(2026, 10, 12), 9) + timedelta(minutes=n),
            f"172.20.42.{100 + n % 100}",
            f"00:00:5e:00:53:{n % 100:02x}",
            source=source,
            interface=interface,
            logid=logid,
            dhcp_msg=message,
        )
    db_session.commit()
    rows = db_session.execute(
        select(
            FgDhcp.source,
            FgDhcp.interface,
            FgDhcp.logid,
            FgDhcp.dhcp_msg,
            trust.refusal(settings, untrusted),
        )
    ).all()
    assert len(rows) == 2 * 3 * 3 * 5
    found = [
        stored_refusal(
            settings,
            untrusted,
            source=source,
            interface=interface,
            logid=logid,
            dhcp_msg=message,
        )
        for source, interface, logid, message, _code in rows
    ]
    assert found == [code for *_values, code in rows]
    # The case decides something: more than one outcome, or (no logid mapped) every row refused.
    assert len(set(found)) > 1 or set(found) == {trust.UNMAPPED}
