"""Records ``goldens/legacy_nat.json``: the Wave 2 engine's answers for the legacy NAT keys.

Wave 3 contract 3.4 E-j: the legacy equivalence test compares the exits that
``legacy_egresses`` converts with **the answers recorded on the Wave 2 tree** before the
breaking switch (``nat.public_ips``, ``nat.wan_transip`` and ``lookup.port_hint`` are removed in
the same merge, so the Wave 2 engine cannot run beside the new one). Run once, on the Wave 2
engine, with ``ARCILOGGER_RECORD_LEGACY_NAT=1``; skipped otherwise. The site and the requests
are the ones ``test_lookup_egress.py`` replays.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from arcilogger_db.settings import get_settings, set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.testing import Evidence, tr

GOLDEN = Path(__file__).parent / "goldens" / "legacy_nat.json"
DAY = date(2026, 10, 12)
NOW = tr(DAY + timedelta(days=1), 9)
#: The line's public address (bridge mode), the FortiGate's WAN address behind the ISP router
#: (router mode), another public address of a legacy list, an unconfigured public NAT address
#: (an IP pool) and a public address no setting names.
A = "203.0.113.200"
A5 = "203.0.113.204"
W = "100.64.12.9"
POOL = "198.51.100.250"
OTHER = "192.0.2.44"
PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
PHONE, PHONE_MAC = "172.20.60.142", "02:00:5e:00:53:64"
#: Router mode (only W is the transip), bridge mode (only A), only the pool, no session.
MOMENTS = {
    "router": tr(DAY, 11, 23, 41),
    "bridge": tr(DAY, 14, 23, 41),
    "pool": tr(DAY, 16, 23, 41),
    "quiet": tr(DAY, 3),
}
#: The legacy settings of every case of contract 2.8.2 (``g``: values stored without
#: validation; ``h``: the FortiGate's WAN address entered as a public address).
CASES: dict[str, dict[str, Any]] = {
    "a": {"public_ips": [], "wan_transip": "", "port_hint": False},
    "b": {"public_ips": [A], "wan_transip": "", "port_hint": False},
    "b2": {
        "public_ips": [f"203.0.113.{200 + i}" for i in range(12)],
        "wan_transip": "",
        "port_hint": False,
    },
    "b3": {
        "public_ips": [f"203.0.113.{200 + i}" for i in range(20)],
        "wan_transip": "",
        "port_hint": False,
    },
    "c": {"public_ips": [], "wan_transip": W, "port_hint": False},
    "d": {"public_ips": [A], "wan_transip": W, "port_hint": False},
    "e": {"public_ips": [], "wan_transip": "", "port_hint": True},
    "f": {"public_ips": [A], "wan_transip": A, "port_hint": False},
    "g": {"public_ips": ["not an address"], "wan_transip": "", "port_hint": False},
    "g2": {"public_ips": ["2001:db8::44", A], "wan_transip": "", "port_hint": False},
    "g3": {"public_ips": ["127.0.0.1", A], "wan_transip": "", "port_hint": False},
    "h": {"public_ips": [W], "wan_transip": "", "port_hint": False},
}
#: Request name -> (moment, request fields).
REQUESTS: dict[str, tuple[str, dict[str, Any]]] = {
    "A+port@router": ("router", {"public_ip": A, "public_port": 52001}),
    "A@router": ("router", {"public_ip": A}),
    "A+port@bridge": ("bridge", {"public_ip": A, "public_port": 52001}),
    "A@bridge": ("bridge", {"public_ip": A}),
    "A+port@pool": ("pool", {"public_ip": A, "public_port": 52001}),
    "A+port@quiet": ("quiet", {"public_ip": A, "public_port": 52001}),
    "A5+port@router": ("router", {"public_ip": A5, "public_port": 51001}),
    "A5+port@bridge": ("bridge", {"public_ip": A5, "public_port": 52001}),
    "W+port@router": ("router", {"public_ip": W, "public_port": 51001}),
    "W@router": ("router", {"public_ip": W}),
    "W+port@bridge": ("bridge", {"public_ip": W, "public_port": 52001}),
    "other+port@router": ("router", {"public_ip": OTHER, "public_port": 51001}),
    "other@router": ("router", {"public_ip": OTHER}),
    "other+dest@router": (
        "router",
        {"public_ip": OTHER, "dest_ip": "203.0.113.10", "dest_port": 443},
    ),
    "other+port@bridge": ("bridge", {"public_ip": OTHER, "public_port": 52001}),
    "other@bridge": ("bridge", {"public_ip": OTHER}),
    "other+port@pool": ("pool", {"public_ip": OTHER, "public_port": 53001}),
    "pool+port@pool": ("pool", {"public_ip": POOL, "public_port": 53001}),
    "dest-only@router": ("router", {"dest_ip": "203.0.113.10", "dest_port": 443}),
}


def legacy_site(session: Session, evidence: Evidence) -> None:
    """Two clients with leases and their sessions at the four moments (fictional values)."""
    evidence.dhcp(tr(DAY, 8, 42, 18), "assigned", PC, PC_MAC, server="dhcp42", hostname="PLN-PC14")
    evidence.dhcp(
        tr(DAY, 8, 37, 52), "assigned", PHONE, PHONE_MAC, server="dhcp60", hostname="Telefon-12"
    )
    router, bridge, pool = MOMENTS["router"], MOMENTS["bridge"], MOMENTS["pool"]
    evidence.traffic(router, PC, 51001, "203.0.113.10", 443, transip=W)
    evidence.traffic(router + timedelta(seconds=40), PHONE, 51002, "203.0.113.20", transip=W)
    evidence.traffic(router + timedelta(seconds=20), PC, 51005, "203.0.113.40", transip=W)
    evidence.traffic(bridge, PC, 52001, "203.0.113.10", 443, transip=A)
    evidence.traffic(bridge + timedelta(seconds=15), PHONE, 52002, "203.0.113.20", transip=A)
    evidence.traffic(pool, PC, 53001, "203.0.113.10", 443, transip=POOL)
    evidence.traffic(pool + timedelta(seconds=10), PHONE, 53002, "203.0.113.20", transip=POOL)
    session.commit()
    rebuild_day(session, DAY, horizon=NOW)
    session.commit()


def answer(outcome: Any) -> dict[str, Any]:
    """What E-j compares: the mode, the matched sessions in order, ``double_nat``."""
    return {
        "mode": outcome.mode,
        "double_nat": outcome.double_nat,
        "matches": [
            [
                m.traffic.srcip,
                m.traffic.srcport,
                m.traffic.transip,
                m.traffic.transport,
                m.kind,
                m.score,
            ]
            for m in outcome.matches
        ],
    }


@pytest.mark.skipif(
    os.environ.get("ARCILOGGER_RECORD_LEGACY_NAT") != "1",
    reason="records the Wave 2 answers once (ARCILOGGER_RECORD_LEGACY_NAT=1)",
)
def test_record_the_wave_2_answers(db_session: Session, evidence: Evidence) -> None:
    legacy_site(db_session, evidence)
    answers: dict[str, dict[str, Any]] = {}
    for case, legacy in CASES.items():
        set_setting(db_session, "lookup.port_hint", legacy["port_hint"], updated_by="tolga")
        db_session.commit()
        values = {
            **get_settings(db_session),
            "nat.public_ips": legacy["public_ips"],
            "nat.wan_transip": legacy["wan_transip"],
        }
        answers[case] = {}
        for name, (moment, fields) in REQUESTS.items():
            try:
                settings = LookupSettings.from_settings(values)
            except ValueError as exc:
                answers[case][name] = {"error": type(exc).__name__}
                continue
            request = LookupRequest(at=MOMENTS[moment], **fields)
            outcome = run_lookup(db_session, request, settings, now=NOW)
            db_session.rollback()
            answers[case][name] = answer(outcome)
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(
        json.dumps(
            {
                "about": (
                    "Answers of the Wave 2 lookup engine for the legacy NAT keys (Wave 3 "
                    "contract 3.4 E-j), recorded with worker/tests/test_record_legacy_nat.py "
                    "before the breaking switch; each match is [srcip, srcport, transip, "
                    "transport, kind, score]. Read by test_lookup_egress.py."
                ),
                "cases": CASES,
                "requests": {k: {"moment": m, **f} for k, (m, f) in REQUESTS.items()},
                "answers": answers,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
