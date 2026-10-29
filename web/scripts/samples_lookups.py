"""The Wave 3 lookups of ``web/e2e/samples/lookups.json`` (contract 2.12 "Samples").

``capture_api_samples.py`` runs :func:`capture` on a **second lookup site**: a database of its own
from the template and a data directory of its own (as the usage samples), so the main site's
lookups, raw lines, data window, seals and sources stay as they are. It passes the capture of
that site (the app, its database and data directory, the request helpers ``json`` and
``error``) and the site's administrator, and merges what :func:`capture` returns into
``lookups.json`` under these keys (the raw lines they cite go to ``raw.json``):

* FortiGate DHCP alone (``build_site(dhcp="fortigate", wan2=True)`` of
  :mod:`arcilogger_api.lookup_testing`): ``fortigate_internal``, ``fortigate_candidates`` (no
  exits: ``unconfigured``), ``hat2_candidates`` (two ISP lines, the second one's sessions),
  ``fortigate_renewal_low`` (an ACK never renewed: ``lease_renewal_not_seen``);
* srcmac (``lookup.srcmac_interfaces`` = vlan42): ``srcmac_only`` (no lease: the MAC of the
  session) and ``srcmac_conflict`` (a lease of another MAC: ``srcmac_mismatch``);
* the internet exits (the E-cases of the worker's ``test_lookup_egress.py``): ``direct_quiet_none``,
  ``fallback_direct``, ``fallback_transip``, ``observed`` and ``observed_no_exits``,
  ``not_logged`` and ``not_logged_seen``, ``public_unknown``, ``public_dynamic``, ``no_egress``,
  ``internal_exit`` and, last (the exit changes after the event), ``changed_after_event``;
* the rest of 2.12: ``clock_offset`` (``source_clock_offset``), ``unconverted_dhcp`` (a FortiGate
  DHCP line that stayed an event row, cited) and ``untrusted_match`` (a session line of an
  unconfigured sender among the candidates).

Every scenario has a day of its own, two days apart in September 2026 (a session search reads
rows up to ``lookup.max_session_seconds`` = one day around its moment), and clients of its own,
so no scenario sees another's rows. The exits are entered (with their change record) before the
first day, each scenario's own just before its lookups; the lookups' ids start at 101, apart from
the main site's. Each lookup is checked for what it is captured for (its mode, its exit
resolution, an explanation key): a scenario that no longer shows it stops the capture.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from arcilogger_api import lookup_testing as site
from arcilogger_api.lookup_testing import (
    PC,
    PC_INTERFACE,
    PC_MAC,
    build_site,
    enter_exits,
    unconverted_dhcp_line,
    unknown_sender_traffic,
)
from arcilogger_core.timeutil import utc_now
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.testing import Evidence, tr

if TYPE_CHECKING:
    from capture_api_samples import Capture

API = "/api/v1"
#: The request letter of these lookups (fictional).
REQUEST = {
    "authority": "İzmir Cumhuriyet Başsavcılığı",
    "file_no": "2026/51877 Soruşturma",
    "request_date": "2026-10-10",
    "reason": "5651 sayılı Kanun kapsamında erişim kayıtları ve kullanıcı bilgisi talebi.",
}
#: The scenario days (site time): two days apart, before the capture's "now" and apart from the
#: main site's days.
DAYS = {
    name: date(2026, 9, 1) + timedelta(days=2 * n)
    for n, name in enumerate(
        (
            "fortigate",
            "renewal",
            "srcmac_only",
            "srcmac_conflict",
            "direct_quiet",
            "fallback_direct",
            "fallback_transip",
            "observed",
            "not_logged",
            "unknown_public",
            "internal_exit",
            "clock",
            "unconverted",
            "untrusted",
            "changed",
        )
    )
}
#: When the exits were entered: before the first day (only ``changed_after_event`` changes one
#: after its moment).
ENTERED = tr(date(2026, 8, 20), 9)
#: The FortiGate's NAT addresses behind the two ISP routers, the lines' public addresses (RFC
#: 5737), the destination of the sessions and an address no exit claims.
T1, T2 = site.WAN, site.WAN2
P1, P2 = "203.0.113.200", "198.51.100.77"
DEST = "203.0.113.10"
UNCLAIMED = "192.0.2.44"
#: An address the implicit double-NAT exit of a site without exits answers.
PUBLIC = "198.51.100.20"
#: The source NAT towards the DMZ, an address the FortiGate used itself, a line that does not
#: pass the FortiGate.
DMZ_NAT, CHANGED = "172.20.99.1", "203.0.113.201"
GUEST = "198.51.100.90"
#: Another client's MAC (a laptop holding the lease ``srcmac_conflict`` contradicts).
OTHER_MAC, OTHER_HOST = "00:00:5e:00:53:77", "MUH-NB03"
#: A FortiGate clock set back by 8 minutes (its lines arrive 8 minutes after their device time).
BEHIND = timedelta(minutes=8)


def direct(public: list[str], **fields: Any) -> dict[str, Any]:
    return {"mode": "direct", "public": public, **fields}


def isp_nat(transips: list[str], **fields: Any) -> dict[str, Any]:
    return {"mode": "isp_nat", "transips": transips, **fields}


HAT_1 = isp_nat([T1], public=[P1], interfaces=["wan1"], label="Ana hat")
HAT_2 = isp_nat([T2], public=[P2], interfaces=["wan2"], label="Yedek hat")
STATIC = {"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2])}


def at(day: str, hour: int = 11, minute: int = 23, second: int = 41) -> datetime:
    return tr(DAYS[day], hour, minute, second)


def clients(day: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """Two clients of VLAN 43 of their own for the scenario ``day`` (address, MAC)."""
    n = list(DAYS).index(day)
    return (
        (f"172.20.43.{10 + 2 * n}", f"00:00:5e:00:53:{0xC0 + 2 * n:02x}"),
        (f"172.20.43.{11 + 2 * n}", f"00:00:5e:00:53:{0xC1 + 2 * n:02x}"),
    )


def write_evidence(capture: Capture) -> None:
    """Every scenario's lines and rows (one Evidence writer, so the line numbers are right),
    then the leases rebuilt day by day, as the nightly job does."""
    factory, layout = capture.factory, capture.layout
    build_site(factory, layout, DAYS["fortigate"], dhcp="fortigate", wan2=True)
    with factory() as session:
        evidence = Evidence(session, layout)
        # MikroTik leases of the exit scenarios' clients for the whole day.
        for day in DAYS:
            if day in ("fortigate", "renewal", "srcmac_only", "srcmac_conflict", "clock"):
                continue
            for k, (ip, mac) in enumerate(clients(day)):
                evidence.dhcp(
                    tr(DAYS[day], 0, 5 + k), "assigned", ip, mac, server="dhcp43",
                    hostname=f"IDR-PC-{ip.rsplit('.', 1)[1]}",
                )  # fmt: skip
        # A FortiGate ACK at 08:00 never renewed while the client's sessions go on (D6, D26).
        ip, mac = "172.20.42.61", "00:00:5e:00:53:61"
        first = tr(DAYS["renewal"], 8)
        evidence.fortigate_dhcp(first, ip, mac, hostname="PLN-PC11")
        for hours in range(0, 25, 2):
            evidence.traffic(first + timedelta(hours=hours), ip, 50000 + hours, transip=T1)
        # The PC with a static address now: its session's srcmac on the listed vlan42.
        evidence.traffic(
            at("srcmac_only"), PC, 51001, DEST, 443, transip=T1, srcintf=PC_INTERFACE,
            dstintf="wan1", srcmac=PC_MAC,
        )  # fmt: skip
        # A laptop's lease of an address whose session carries the PC's MAC as srcmac (D10).
        ip = "172.20.42.58"
        evidence.fortigate_dhcp(tr(DAYS["srcmac_conflict"], 8), ip, OTHER_MAC, hostname=OTHER_HOST)
        evidence.traffic(
            at("srcmac_conflict"), ip, 51001, DEST, 443, transip=T1, srcintf=PC_INTERFACE,
            dstintf="wan1", srcmac=PC_MAC,
        )  # fmt: skip
        # E-c: the ISP NAT line's session while the direct line is quiet.
        _a, (b, _mac) = clients("direct_quiet")
        evidence.traffic(at("direct_quiet"), b, 51002, DEST, 443, transip=T2)
        # E-c2: line 1 double-NATed (the unclaimed T1 on wan1), line 2 active on wan2.
        (a, _mac), (b, _mac) = clients("fallback_direct")
        evidence.traffic(at("fallback_direct"), a, 51001, DEST, 443, transip=T1, dstintf="wan1")
        later = at("fallback_direct") + timedelta(seconds=5)
        evidence.traffic(later, b, 51002, DEST, 443, transip=T2, dstintf="wan2")
        # E-k2: hat-2's NAT address quiet while an unclaimed one is active.
        (a, _mac), _b = clients("fallback_transip")
        evidence.traffic(at("fallback_transip"), a, 51001, DEST, 443, transip="100.64.12.77")
        # E-e2/E-e3: an address the FortiGate used itself, beside another client's session.
        (a, _mac), (b, _mac) = clients("observed")
        evidence.traffic(at("observed"), a, 51001, DEST, 443, transip=CHANGED)
        evidence.traffic(at("observed"), b, 51002, DEST, 443, transip=T2)
        # E-e: an unclaimed NAT address and hat-2's.
        (a, _mac), (b, _mac) = clients("unknown_public")
        evidence.traffic(at("unknown_public"), a, 51001, DEST, 443, transip=T1)
        evidence.traffic(at("unknown_public"), b, 51002, DEST, 443, transip=T2)
        # E-q: sessions to the DMZ through its NAT address every minute.
        (a, _mac), _b = clients("internal_exit")
        for minute in range(-5, 6):
            evidence.traffic(
                at("internal_exit") + timedelta(minutes=minute), a, 40020 + minute,
                "172.20.99.10", 8443, transip=DMZ_NAT, dstintf="dmz",
            )  # fmt: skip
        # D28: the FortiGate's lines around the moment arrive 8 minutes late.
        ip, mac = "172.20.42.66", "00:00:5e:00:53:66"
        evidence.fortigate_dhcp(tr(DAYS["clock"], 8), ip, mac, hostname="PLN-PC16")
        evidence.traffic(at("clock"), ip, 51001, DEST, 443, transip=T1)
        for k in range(20):
            moment = at("clock") - timedelta(minutes=50) + timedelta(minutes=5 * k)
            evidence.heartbeat(moment, source="fortigate", received=moment + BEHIND)
        # D8: a FortiGate ACK of the day before that stayed an event row (loaded before the
        # upgrade), and the client's session.
        ip, mac = "172.20.42.67", "00:00:5e:00:53:67"
        unconverted_dhcp_line(
            evidence, at("unconverted") - timedelta(days=1), ip, mac, hostname="PLN-PC17",
            lease=604800,
        )  # fmt: skip
        evidence.traffic(at("unconverted"), ip, 51001, DEST, 443, transip=T1)
        # E-p2: three sessions through T1 and one line of an unconfigured sender among them.
        for n in range(3):
            evidence.traffic(
                at("untrusted") + timedelta(seconds=n), f"172.20.42.{110 + n}", 52000 + n,
                transip=T1,
            )  # fmt: skip
        (a, _mac), _b = clients("untrusted")
        unknown_sender_traffic(evidence, at("untrusted"), a, 51001, transip=T1)
        # E-k4: the session through hat-2's address.
        _a, (b, _mac) = clients("changed")
        evidence.traffic(at("changed"), b, 51002, DEST, 443, transip=T2, dstintf="wan2")
        session.commit()
        day = DAYS["fortigate"] + timedelta(days=1)
        while day <= DAYS["changed"] + timedelta(days=1):
            rebuild_day(session, day, horizon=utc_now())
            session.commit()
            day += timedelta(days=1)


def body(moment: datetime, **query: Any) -> dict[str, Any]:
    return {
        "request": dict(REQUEST),
        "query": {"at": moment.isoformat(), "tolerance_seconds": 60, **query},
    }


def explanation_keys(value: Any) -> set[str]:
    """Every explanation key in an answer (the outcome's, its chains' and alternatives')."""
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "explanations" and isinstance(item, list):
                found |= {entry["key"] for entry in item}
            else:
                found |= explanation_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= explanation_keys(item)
    return found


class Lookups:
    """The site's lookups through ``POST /lookup/5651``, each checked for what it shows."""

    def __init__(self, capture: Capture, admin: Any) -> None:
        self.capture = capture
        self.admin = admin
        self.out: dict[str, Any] = {}

    def exits(self, value: Mapping[str, Any], *, at: datetime = ENTERED, by: str = "tolga") -> None:
        enter_exits(self.capture.factory, value, at=at, by=by)

    def run(
        self,
        name: str,
        query: dict[str, Any],
        *,
        mode: str | None = None,
        resolution: tuple[str, list[str]] | None = None,
        keys: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        answer: dict[str, Any] = self.capture.json(
            self.admin.post(f"{API}/lookup/5651", json=query), 201
        )
        outcome = answer["outcome"]
        egress = outcome.get("egress")
        found = (
            None
            if egress is None
            else (egress["resolution"], [entry["name"] for entry in egress["egresses"]])
        )
        problems = []
        if mode is not None and outcome["mode"] != mode:
            problems.append(f"mode {outcome['mode']}")
        if resolution is not None and found != resolution:
            problems.append(f"exit {found}")
        if missing := set(keys) - explanation_keys(answer):
            problems.append(f"no {sorted(missing)}")
        if problems:
            dump = json.dumps(outcome, ensure_ascii=False)[:3000]
            raise SystemExit(f"lookup sample {name}: {', '.join(problems)}: {dump}")
        self.out[name] = answer
        return answer


def capture(capture: Capture, admin: Any) -> dict[str, Any]:
    """The Wave 3 lookups (module text): ``{lookups.json key: LookupOut}``."""
    with capture.factory() as session:
        session.execute(text("SELECT setval(pg_get_serial_sequence('lookups', 'id'), 100)"))
        session.commit()
    write_evidence(capture)
    run = Lookups(capture, admin)

    # FortiGate DHCP alone (the example chain), with and without exits; the second ISP line.
    run.exits({})
    run.run(
        "fortigate_internal",
        body(at("fortigate"), internal_ip=PC),
        mode="internal",
        keys=("fortigate_format_unverified",),
    )
    run.run(
        "fortigate_candidates",
        body(at("fortigate"), public_ip=PUBLIC, dest_ip=DEST, dest_port=443),
        mode="candidates",
        resolution=("unconfigured", [""]),  # the implicit double-NAT exit
    )
    run.exits({"hat-1": HAT_1, "hat-2": HAT_2})
    run.run(
        "hat2_candidates",
        body(at("fortigate"), public_ip=P2),
        mode="candidates",
        resolution=("public", ["hat-2"]),
        keys=("egress_search",),
    )
    run.run(
        "fortigate_renewal_low",
        body(tr(DAYS["renewal"], 23), internal_ip="172.20.42.61"),
        mode="internal",
        keys=("lease_renewal_not_seen",),
    )

    # srcmac on vlan42: the MAC of a session without a lease, and against another MAC's lease.
    capture.setting("lookup.srcmac_interfaces", [PC_INTERFACE])
    run.run(
        "srcmac_only",
        body(at("srcmac_only"), internal_ip=PC),
        mode="internal",
        keys=("srcmac_only", "no_lease"),
    )
    run.exits({})
    run.run(
        "srcmac_conflict",
        body(at("srcmac_conflict"), public_ip=PUBLIC),
        mode="candidates",
        keys=("srcmac_mismatch",),
    )

    # The internet exits (the E-cases).
    run.exits(STATIC)
    run.run(
        "direct_quiet_none",
        body(at("direct_quiet"), public_ip=P1, public_port=51001),
        mode="none",
        resolution=("public", ["hat-1"]),
    )
    run.exits(
        {
            "hat-1": direct([P1], interfaces=["wan1"]),
            "hat-2": isp_nat([T2], interfaces=["wan2"]),
        }
    )
    run.run(
        "fallback_direct",
        body(at("fallback_direct"), public_ip=P1, public_port=51001),
        mode="candidates",
        resolution=("fallback", ["hat-1"]),
        keys=("public_ip_not_transip",),
    )
    run.exits(STATIC)
    run.run(
        "fallback_transip",
        body(at("fallback_transip"), public_ip=T2, public_port=51001),
        resolution=("fallback", ["hat-2"]),
        keys=("public_ip_not_transip",),
    )
    run.run(
        "observed",
        body(at("observed"), public_ip=CHANGED, public_port=51001),
        mode="exact",
        resolution=("observed", []),
        keys=("egress_observed_not_configured",),
    )
    run.exits({})
    run.run(
        "observed_no_exits",
        body(at("observed"), public_ip=CHANGED, public_port=51001),
        mode="exact",
        resolution=("observed", []),
    )
    run.exits({"hat-1": direct([P1]), "misafir": {"mode": "not_logged", "public": [GUEST]}})
    run.run(
        "not_logged",
        body(at("not_logged"), public_ip=GUEST, public_port=51001),
        mode="none",
        resolution=("public", ["misafir"]),
        keys=("egress_not_logged",),
    )
    # The guest line's address is the NAT address of a session at the moment after all.
    with capture.factory() as session:
        (a, _mac), _b = clients("not_logged")
        Evidence(session, capture.layout).traffic(
            at("not_logged"), a, 51001, DEST, 443, transip=GUEST
        )
        session.commit()
    run.run(
        "not_logged_seen",
        body(at("not_logged"), public_ip=GUEST, public_port=51001),
        mode="exact",
        resolution=("observed", ["misafir"]),
        keys=("egress_not_logged_but_seen",),
    )
    run.exits({"hat-1": direct([P1]), "hat-2": isp_nat([T2])})
    run.run(
        "public_unknown",
        body(at("unknown_public"), public_ip=UNCLAIMED, public_port=41000),
        mode="candidates",
        resolution=("public_unknown", ["hat-2"]),
        keys=("egress_public_unknown",),
    )
    run.exits({"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2], public_dynamic=True)})
    run.run(
        "public_dynamic",
        body(at("unknown_public"), public_ip=P2, public_port=41000),
        resolution=("public", ["hat-2"]),
        keys=("egress_public_dynamic",),
    )
    run.exits(STATIC)
    run.run(
        "no_egress",
        body(at("unknown_public"), public_ip=UNCLAIMED, public_port=41000),
        mode="none",
        resolution=("no_egress", ["hat-1", "hat-2"]),
        keys=("public_ip_no_egress",),
    )
    dmz = {"mode": "internal", "transips": [DMZ_NAT], "interfaces": ["dmz"]}
    run.exits({"hat-1": direct([P1]), "dmz": dmz})
    run.run(
        "internal_exit",
        body(at("internal_exit"), public_ip=DMZ_NAT, public_port=40020),
        mode="none",
        resolution=("transip", ["dmz"]),
        keys=("egress_internal",),
    )

    # A source clock 8 minutes off, a DHCP line that stayed an event row, an untrusted sender.
    run.exits({"wan": isp_nat([T1])})
    run.run(
        "clock_offset",
        body(at("clock"), public_ip=T1, public_port=51001, protocol="tcp"),
        mode="exact",
        keys=("source_clock_offset",),
    )
    unconverted = run.run(
        "unconverted_dhcp",
        body(at("unconverted"), internal_ip="172.20.42.67"),
        keys=("fortigate_dhcp_not_converted",),
    )
    if not unconverted["outcome"].get("unconverted_dhcp"):
        raise SystemExit("lookup sample unconverted_dhcp cites no event row")
    run.exits({"hat-1": isp_nat([T1], public=[P1])})
    untrusted = run.run(
        "untrusted_match",
        body(at("untrusted"), public_ip=P1, public_port=51001),
        mode="candidates",
        keys=("session_from_unknown_source",),
    )
    if not any(match.get("untrusted_sources") for match in untrusted["outcome"]["matches"]):
        raise SystemExit("lookup sample untrusted_match marks no match")

    # E-k4, last: hat-2 converted again by the upgrade the day after the session.
    base = {
        "hat-1": direct([P1], interfaces=["wan1"]),
        "hat-2": isp_nat([T2], public=[P2], interfaces=["wan2"]),
    }
    run.exits(base)
    renumbered = {**base, "hat-2": {**base["hat-2"], "public": [P2, "198.51.100.78"]}}
    run.exits(renumbered, at=tr(DAYS["changed"] + timedelta(days=1), 9), by=":migration")
    run.run(
        "changed_after_event",
        body(at("changed"), public_ip=P2),
        resolution=("public", ["hat-2"]),
        keys=("egress_changed_after_event",),
    )
    return run.out
