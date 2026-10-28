"""The load generator (``tools/loadgen``, spec 17): deterministic, and every line it makes is
understood by the real parsers without a parse failure — synthetic lines, verify on site."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import ipaddress
import itertools
import json
import os
import socket
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from arcilogger_collector.service import Collector
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line, parse_line
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.rowcheck import check_row
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import EXAMPLE_SETTINGS, EXAMPLE_VLANS
from arcilogger_ingest.parsers import LinePosition
from arcilogger_ingest.parsers import parse_line as parse_message

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "loadgen" / "arcilogger_loadgen.py"
#: A Friday and a Saturday: churn across a weekend (leases expire, few people come in).
FRIDAY, SATURDAY = date(2026, 10, 9), date(2026, 10, 10)


@cache
def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def simulate(seed: int = 7, users: int = 10, days: tuple[date, ...] = (FRIDAY, SATURDAY)) -> Any:
    lg = loadgen()
    settings = lg.Settings(seed=seed, users=users, intensity=12.0)
    return list(lg.Simulation(settings).run(days[0], days[-1]))


@pytest.fixture(scope="module")
def outputs() -> Any:
    return simulate()


def spool_digest(outputs: Any) -> str:
    lg = loadgen()
    addresses = lg.senders(lg.Settings())
    lines = lg.spool_lines(outputs, addresses=addresses)
    hasher = hashlib.sha256()
    for key in sorted(lines):
        hasher.update(repr(key).encode())
        hasher.update(b"".join(lines[key]))
    return hasher.hexdigest()


def test_help_runs_as_a_script() -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "synthetic, verify on site" in done.stdout
    for command in ("spool", "send", "truth", "stats"):
        assert command in done.stdout


def test_the_same_seed_gives_the_same_bytes(outputs: Any) -> None:
    assert spool_digest(outputs) == spool_digest(simulate())
    assert spool_digest(outputs) != spool_digest(simulate(seed=8))
    first = outputs[0]
    again = simulate()[0]
    assert [s.sessionid for s in first.sessions] == [s.sessionid for s in again.sessions]
    assert first.leases == again.leases


#: :func:`spool_digest` of the module's default days. The Wave 3 options (``--dhcp-server``,
#: ``--srcmac``, ``--egress``) default to the earlier behaviour, so existing users of the
#: generator get the same bytes (contract 2.14).
DEFAULT_DIGEST = "d7e1141b96f00644de78a857cf30f3302534e7e05b86d675bcc142229b2d22e9"


def test_the_default_plan_keeps_its_bytes(outputs: Any) -> None:
    assert spool_digest(outputs) == DEFAULT_DIGEST


def _expected_table(kind: str) -> str | None:
    return {
        "traffic": "fg_traffic",
        "webfilter": "fg_web",
        "dns": "fg_dns",
        "app-ctrl": "fg_app",
        "perf-stats": "fg_event",
        "dhcp.assigned": "mt_dhcp",
        "dhcp.deassigned": "mt_dhcp",
        "fg-dhcp.ack": "fg_dhcp",
        "fg-dhcp.release": "fg_dhcp",
        "arp": "mt_arp",
        "heartbeat": "mt_event",
        "link": "mt_interface",
        "firewall": "mt_fw",
    }.get(kind)


def parsed(outputs: Any) -> Iterator[tuple[str, Any, Any]]:
    """``(kind, message, outcome)`` of every line, through the spool format and the real
    parsers as the ingest service reads them; asserts no failure and complete, valid rows of
    the expected table."""
    lg = loadgen()
    types = {source.name: source.type for source in lg.DEFAULT_SOURCES}
    addresses = lg.senders(lg.Settings())
    messages = {
        (m.source, m.received.astimezone(SITE_TZ).date(), m.wire): m
        for output in outputs
        for m in output.messages
    }
    for (source, day), lines in lg.spool_lines(outputs, addresses=addresses).items():
        offset = 0
        for number, line in enumerate(lines, 1):
            record = parse_line(line)
            assert record.sender == addresses[source]
            outcome = parse_message(
                record, LinePosition(source, types[source], day, number, offset)
            )
            offset += len(line)
            assert outcome.error is None, (outcome.error, outcome.detail, record.message)
            assert check_row("messages", outcome.message, complete=True) == []
            message = messages[(source, day, record.message)]
            tables = [row.table for row in outcome.rows]
            assert tables == [_expected_table(message.kind)], (message.kind, record.message)
            for row in outcome.rows:
                assert check_row(row.table, row.values, complete=True) == [], record.message
            yield message.kind, message, outcome


def test_every_line_parses_without_failures_into_the_expected_table(outputs: Any) -> None:
    seen = Counter(kind for kind, _, _ in parsed(outputs))
    # Everything the spec lists is there.
    assert {"traffic", "webfilter", "dns", "app-ctrl", "dhcp.assigned", "arp", "heartbeat"} <= set(
        seen
    )
    assert seen["dhcp.deassigned"] > 0  # Saturday: Friday's leases expire, guests leave


def test_sessions_are_joined_and_attributable(outputs: Any) -> None:
    """Ground truth: the sessions' FortiGate lines share the session id, the NAT port is unique
    while a session runs, the source address is the device's lease, interim deltas add up."""
    friday = outputs[0]
    assert friday.sessions
    by_id: dict[int, list[Any]] = {}
    for message in friday.messages:
        if message.source != "fortigate" or message.kind not in (
            "traffic",
            "webfilter",
            "app-ctrl",
        ):
            continue
        wire = message.wire.decode()
        sessionid = int(wire.split(" sessionid=", 1)[1].split(" ", 1)[0])
        by_id.setdefault(sessionid, []).append(message)
    running: list[Any] = []
    for session in friday.sessions:
        kinds = Counter(m.kind for m in by_id[session.sessionid])
        assert kinds["webfilter"] == 1
        assert kinds["traffic"] == 1 + session.interim_logs
        assert kinds["app-ctrl"] == (0 if session.blocked else 1)
        assert session.transip == "100.64.12.9"
        overlapping = [
            other
            for other in running
            if other.transport == session.transport and other.end > session.start
        ]
        assert overlapping == []
        running.append(session)
        assert (session.user is None) == (session.kind in ("admin", "guest")), session
        if session.kind != "admin":  # every device but the static admin PC has a DHCP lease
            held = [
                lease
                for lease in friday.leases
                if lease.ip == session.srcip
                and lease.mac == session.mac
                and lease.start <= session.start
                and (lease.end is None or session.start <= lease.end)
            ]
            assert held, session
    long_sessions = [s for s in friday.sessions if s.interim_logs >= 2]
    assert long_sessions, "no session long enough for interim logs"
    session = long_sessions[0]
    lines = sorted(by_id[session.sessionid], key=lambda m: m.at)
    traffic = [m.wire.decode() for m in lines if m.kind == "traffic"]

    def value(line: str, key: str) -> int:
        return int(line.split(f" {key}=", 1)[1].split(" ", 1)[0])

    assert all('logid="0000000020"' in line for line in traffic[:-1])
    assert 'logid="0000000013"' in traffic[-1]
    assert sum(value(line, "sentdelta") for line in traffic) == value(traffic[-1], "sentbyte")
    assert sum(value(line, "durationdelta") for line in traffic) == value(traffic[-1], "duration")


def test_nat_keeps_the_source_port_unless_it_is_taken() -> None:
    """The simulation's assumption (verify on site): ``transport`` is the source port; only a port
    that another session still holds is moved to the next free one. Ports are given out in time
    order over all devices, not one user's whole day after the other (which moved about one in
    ten)."""
    sessions = [s for output in simulate(users=40) for s in output.sessions]
    moved = [s for s in sessions if s.transport != s.srcport]
    assert len(sessions) > 4000
    assert len(moved) <= len(sessions) // 50, f"{len(moved)} of {len(sessions)} moved"
    assert all(0 < (s.transport - s.srcport) % 16384 <= 50 for s in moved)


def _run_script(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**os.environ, **env},
        timeout=300,
    )


def test_truth_is_utf8_on_a_windows_console_code_page() -> None:
    """Names and departments are Turkish; a cp1252 console (Windows, English locale) must not
    make ``truth > truth.jsonl`` fail."""
    done = _run_script(
        "--users", "6", "--day", FRIDAY.isoformat(), "truth", PYTHONIOENCODING="cp1252"
    )
    assert done.returncode == 0, done.stderr
    records = [json.loads(line) for line in done.stdout.splitlines()]
    assert records
    assert all(record["note"] == "synthetic, verify on site" for record in records)
    names = {
        str(record[key])
        for record in records
        for key in ("person", "department")
        if record.get(key)
    }
    assert any(not name.isascii() for name in names), names


def test_too_many_users_for_the_wifi_pool_is_a_clear_error() -> None:
    done = _run_script("--users", "400", "--day", FRIDAY.isoformat(), "stats")
    assert done.returncode == 2
    assert "Traceback" not in done.stderr
    assert "HATA / ERROR: VLAN 60 DHCP havuzu" in done.stderr
    assert "lower --users" in done.stderr


def test_spool_refuses_a_data_folder_in_use(tmp_path: Path) -> None:
    """Synthetic lines must never land in a real spool: a folder with ARCILOGGER state, sealed
    days or spool files it did not write is refused unless the operator says it is a test."""
    lg = loadgen()
    data = tmp_path / "data"
    layout = Layout(data)
    (layout.state / "seal").mkdir(parents=True)
    (layout.state / "seal" / "2026-10-08.json").write_text("{}", encoding="utf-8")
    args = ("--users", "2", "--intensity", "2", "--day", FRIDAY.isoformat(), "--only", "fortigate")
    refused = _run_script(*args, "spool", "--data-dir", str(data))
    assert refused.returncode == 2
    assert "state/" in refused.stderr
    assert not layout.spool.exists()
    assert not (data / lg.DEMO_MARKER).exists()

    forced = _run_script(*args, "spool", "--data-dir", str(data), "--i-know-this-is-not-production")
    assert forced.returncode == 0, forced.stderr
    assert "synthetic" in (data / lg.DEMO_MARKER).read_text(encoding="utf-8").lower()
    # The marked folder is the generator's own: the next day goes in without the flag.
    nextday = (SATURDAY + timedelta(days=1)).isoformat()
    later = _run_script("--users", "2", "--day", nextday, "--only", "fortigate", "spool",
                        "--data-dir", str(data))  # fmt: skip
    assert later.returncode == 0, later.stderr

    real = tmp_path / "real"
    Layout(real).spool_file("fortigate", FRIDAY).parent.mkdir(parents=True)
    Layout(real).spool_file("fortigate", FRIDAY).write_bytes(b"collected line\n")
    with pytest.raises(lg.LoadgenError, match="spool/"):
        lg.write_spool(
            real, simulate(users=2, days=(SATURDAY,)), addresses=lg.senders(lg.Settings())
        )
    assert Layout(real).spool_file("fortigate", FRIDAY).read_bytes() == b"collected line\n"


def test_phones_use_random_mac_addresses_and_guests_short_leases(outputs: Any) -> None:
    lg = loadgen()
    users = lg.build_users(lg.Settings(seed=7, users=10))
    for user in users:
        mac = user.phone.mac_for(FRIDAY, 7)
        assert int(mac.split(":")[0], 16) & 0x02, mac  # locally administered
        assert not int(user.pc.base_mac.split(":")[0], 16) & 0x02
    rotating = [u.phone for u in lg.build_users(lg.Settings(seed=7, users=60)) if u.phone.rotating]
    assert rotating
    assert rotating[0].mac_for(FRIDAY, 7) != rotating[0].mac_for(SATURDAY, 7)
    # The guest Wi-Fi's 1 h leases (the tests' example network: lease.server_seconds).
    assert lg.LEASE_SECONDS_BY_SERVER == {"dhcp61": 3600}
    guests = [lease for o in outputs for lease in o.leases if lease.server == "dhcp61"]
    assert guests
    for lease in guests:
        assert lease.end is not None
        assert lease.end - lease.start >= timedelta(hours=1)
    # Guests browse through the firewall too: anonymous sessions from their leases.
    guest_sessions = [s for o in outputs for s in o.sessions if s.kind == "guest"]
    assert guest_sessions
    assert all(s.user is None and s.person is None for s in guest_sessions)
    assert {s.vlan for s in guest_sessions} == {61}


def test_the_simulated_infrastructure_has_documentation_mac_addresses() -> None:
    """Fictional devices: MAC addresses from the documentation range (RFC 7042) that follow the
    device's address, except the printers' vendor prefix (so the OUI lookup names a vendor, as
    for real printers)."""
    devices = loadgen().infrastructure()
    kinds = {device.kind for device in devices}
    assert kinds == {"admin", "server", "switch", "firewall", "ap", "printer"}
    for device in devices:
        assert device.static_ip is not None, device
        assert device.server is None, device
        if device.kind != "printer":
            host = int(device.static_ip.rsplit(".", 1)[1])
            assert device.base_mac == f"00:00:5e:00:53:{host:02x}", device
    assert len({device.base_mac for device in devices}) == len(devices)


def test_the_simulation_uses_the_example_plan() -> None:
    """The simulated network is the tests' fictional example plan (arcilogger_db.testing): its
    three department VLANs, everyone's phone on the one staff Wi-Fi, guests on the guest Wi-Fi
    with its short leases, network devices static in VLAN 10, servers in VLAN 20, the firewall
    on the transit link; every address lies in its VLAN's subnet."""
    lg = loadgen()
    plan = {vlan.vlan_id: vlan for vlan in EXAMPLE_VLANS}
    assert {v.vlan_id: v.name_tr for v in EXAMPLE_VLANS if v.department} == lg.DEPARTMENTS
    assert plan[lg.GUEST_WIFI].guest
    staff = plan[lg.STAFF_WIFI]
    assert staff.dhcp_server == f"dhcp{lg.STAFF_WIFI}"
    assert not staff.guest
    assert not staff.department
    assert EXAMPLE_SETTINGS["lease.server_seconds"] == lg.LEASE_SECONDS_BY_SERVER
    users = lg.build_users(lg.Settings(seed=7, users=12))
    assert {user.department for user in users} == set(lg.DEPARTMENTS.values())
    for user in users:
        assert user.pc.server == plan[user.vlan].dhcp_server
        assert (user.phone.vlan, user.phone.server) == (60, "dhcp60")
    transit = plan[None]
    for device in lg.infrastructure():
        assert device.static_ip is not None
        address = ipaddress.ip_address(device.static_ip)
        if device.kind == "firewall":
            assert address in ipaddress.ip_network(transit.subnet), device
            continue
        assert device.vlan == (20 if device.kind == "server" else 10), device
        assert address in ipaddress.ip_network(plan[device.vlan].subnet), device
    assert ipaddress.ip_address(lg.DNS_SERVER) in ipaddress.ip_network(plan[20].subnet)


# -- Wave 3 site plans (contract 2.14, 3.7) -------------------------------------------------------

#: Thursday to Saturday: leases carried over midnight renew when their device is back.
THURSDAY = date(2026, 10, 8)
#: The two exits of the contract's dual-WAN site (3.7).
EXITS = ("hat-1:100.64.12.9:wan1:60", "hat-2:100.64.13.9:wan2:40")


def simulate_plan(
    days: tuple[date, ...] = (THURSDAY, FRIDAY, SATURDAY),
    users: int = 10,
    intensity: float = 12.0,
    **options: Any,
) -> Any:
    lg = loadgen()
    settings = lg.Settings(seed=7, users=users, intensity=intensity, **options)
    return list(lg.Simulation(settings).run(days[0], days[-1]))


def egresses(*specs: str) -> Any:
    return tuple(loadgen().parse_egress(spec) for spec in specs)


@pytest.fixture(scope="module")
def fortigate_site() -> Any:
    return simulate_plan(dhcp_server="fortigate", srcmac=True)


@pytest.fixture(scope="module")
def fortigate_rows(fortigate_site: Any) -> list[tuple[str, Any, dict[str, Any]]]:
    """``(kind, message, typed row values)`` of every line of :func:`fortigate_site`."""
    return [(kind, message, outcome.rows[0].values) for kind, message, outcome in
            parsed(fortigate_site)]  # fmt: skip


@pytest.mark.parametrize(
    "options",
    [
        {"dhcp_server": "fortigate"},
        {"dhcp_server": "both"},
        {"srcmac": True},
        {"egresses": EXITS},
        {"dhcp_server": "fortigate", "srcmac": True, "egresses": EXITS},
    ],
    ids=["fortigate", "both", "srcmac", "egress", "all"],
)
def test_each_new_option_is_deterministic(options: dict[str, Any]) -> None:
    if "egresses" in options:
        options = {**options, "egresses": egresses(*options["egresses"])}
    days = (FRIDAY,)
    first = simulate_plan(days, **options)
    assert spool_digest(first) == spool_digest(simulate_plan(days, **options))
    assert spool_digest(first) != spool_digest(simulate_plan(days))
    assert first[0].sessions == simulate_plan(days, **options)[0].sessions
    assert first[0].leases == simulate_plan(days, **options)[0].leases


def _lease_seconds(server: str | None) -> int:
    lg = loadgen()
    return int(lg.LEASE_SECONDS_BY_SERVER.get(server or "", lg.LEASE_SECONDS))


def test_a_fortigate_only_site_has_fortigate_dhcp_and_no_mikrotik_line(
    fortigate_site: Any, fortigate_rows: list[tuple[str, Any, dict[str, Any]]]
) -> None:
    """``--dhcp-server fortigate``: only the FortiGate sends; every DHCP line is an ``fg_dhcp``
    row whose logid the shipped settings map and whose values are the truth's lease (the
    cross-check with the shipped parser), and client traffic comes in on the client's VLAN."""
    lg = loadgen()
    assert {m.source for o in fortigate_site for m in o.messages} == {"fortigate"}
    kinds = Counter(kind for kind, _, _ in fortigate_rows)
    assert kinds["fg-dhcp.ack"] > 0
    assert kinds["fg-dhcp.release"] > 0  # Thursday's and Friday's guests leave
    assert not {"arp", "heartbeat", "link", "firewall", "dhcp.assigned", "dhcp.deassigned"} & set(
        kinds
    )
    logids = DEFAULT_SETTINGS["dhcp.fortigate_logids"]
    dhcp_msgs = DEFAULT_SETTINGS["dhcp.fortigate_dhcp_msgs"]
    leases = [lease for o in fortigate_site for lease in o.leases]
    sessions = {s.sessionid: s for o in fortigate_site for s in o.sessions}
    checked: Counter[str] = Counter()
    for kind, message, row in fortigate_rows:
        if kind.startswith("fg-dhcp."):
            release = kind == "fg-dhcp.release"
            assert logids[row["logid"]] == ("deassigned" if release else "assigned")
            if row["logid"] in dhcp_msgs:
                assert row["dhcp_msg"].lower() in {m.lower() for m in dhcp_msgs[row["logid"]]}
            (lease,) = [
                lease
                for lease in leases
                if (lease.ip, lease.mac) == (row["ip"], row["mac"])
                and lease.start <= message.at
                and (lease.end is None or message.at <= lease.end)
            ]
            assert lease.evidence == "fg_dhcp"
            assert row["interface"] == f"vlan{lease.vlan}"
            assert row.get("hostname") == lease.hostname
            assert row.get("lease") == (None if release else _lease_seconds(lease.server))
            assert (row["time"], row["device_time"]) == (message.at, True)
            assert f' mac="{lease.mac.upper()}" ' in message.wire.decode()  # upper case, as F6
            checked[kind] += 1
        elif kind == "traffic":
            session = sessions[int(row["sessionid"])]
            assert (row["srcip"], int(row["srcport"])) == (session.srcip, session.srcport)
            assert row["srcintf"] == f"vlan{session.vlan}"
            assert row["srcmac"] == session.mac  # the client's own MAC
            assert f' srcmac="{session.mac}" ' in message.wire.decode()  # lower case on the wire
            assert (row["transip"], int(row["transport"])) == (session.transip, session.transport)
            assert row["dstintf"] == "wan1"
            assert session.egress is None
            checked[kind] += 1
    assert checked["fg-dhcp.ack"] == kinds["fg-dhcp.ack"]
    assert checked["traffic"] == kinds["traffic"]
    # Static devices have no lease evidence on such a site: listed with "none".
    dynamic = [lease for lease in leases if lease.server is not None]
    assert {lease.evidence for lease in dynamic} == {"fg_dhcp"}
    statics = [lease for lease in leases if lease.server is None]
    assert {lease.device for lease in statics} == {
        device.key for device in lg.infrastructure() if device.kind != "firewall"
    }
    assert {lease.evidence for lease in statics} == {"none"}
    assert all(lease.end is None for lease in statics)


def test_fortigate_renewals_come_at_half_the_lease(
    fortigate_site: Any, fortigate_rows: list[tuple[str, Any, dict[str, Any]]]
) -> None:
    """A client renews once half its lease has passed: at that moment while it is connected,
    else as soon as it is back. So no lease runs out while its device is in use, and a lease
    ends one lease time after its last ACK (unlogged) or with a guest's RELEASE."""
    acks: dict[tuple[str, str], list[datetime]] = {}
    releases: dict[tuple[str, str], list[datetime]] = {}
    for kind, message, row in fortigate_rows:
        if kind in ("fg-dhcp.ack", "fg-dhcp.release"):
            found = acks if kind == "fg-dhcp.ack" else releases
            found.setdefault((row["ip"], row["mac"]), []).append(message.at)
    leases = [lease for o in fortigate_site for lease in o.leases if lease.server is not None]
    gaps: Counter[str] = Counter()
    for lease in leases:
        half = timedelta(seconds=_lease_seconds(lease.server)) / 2
        times = [
            at
            for at in acks[(lease.ip, lease.mac)]
            if lease.start <= at and (lease.end is None or at <= lease.end)
        ]
        assert times[0] == lease.start
        for before, after in itertools.pairwise(times):
            assert after - before >= half, lease
            gaps["half" if after - before == half else "back"] += 1
        released = [
            at
            for at in releases.get((lease.ip, lease.mac), [])
            if lease.start <= at and (lease.end is None or at <= lease.end)
        ]
        if lease.server == "dhcp61":  # a guest's phone releases when it leaves
            assert released == [lease.end]
        else:
            assert released == []
            last = times[-1] + 2 * half
            assert lease.end is None or lease.end == last, lease
    assert gaps["half"] > 0  # guests (1 h leases) renew every 30 minutes while connected
    assert gaps["back"] > 0  # a PC back the next morning renews its yesterday's lease
    for session in (s for o in fortigate_site for s in o.sessions):
        if session.kind == "admin":  # static
            continue
        pair = (session.srcip, session.mac)
        lease = timedelta(seconds=_lease_seconds("dhcp61" if session.vlan == 61 else None))
        last = max(at for at in acks[pair] if at <= session.start)
        assert session.start < last + lease, session
        assert not [at for at in releases.get(pair, []) if last < at < session.start], session


def test_a_fortigate_address_is_never_handed_out_while_its_lease_runs() -> None:
    """Devices are simulated one after the other: an address whose lease ends later in the day
    must not go to a device that comes earlier (two MACs ACKed one address at once)."""
    days = (date(2026, 10, 5), FRIDAY)
    site = simulate_plan(days, users=60, intensity=1.0, dhcp_server="fortigate")
    far = datetime(9999, 1, 1, tzinfo=UTC)
    by_ip: dict[str, list[Any]] = {}
    for lease in (lease for o in site for lease in o.leases if lease.evidence == "fg_dhcp"):
        by_ip.setdefault(lease.ip, []).append(lease)
    assert len(by_ip) > 100
    for leases in by_ip.values():
        for first, second in itertools.combinations(leases, 2):
            assert first.mac == second.mac or not (
                first.start < (second.end or far) and second.start < (first.end or far)
            ), (first, second)


def test_both_keeps_mikrotik_for_the_departments_and_fortigate_for_the_wifi() -> None:
    lg = loadgen()
    site = simulate_plan(dhcp_server="both", srcmac=True)
    leases = [lease for o in site for lease in o.leases]
    assert {(lease.vlan, lease.evidence) for lease in leases} == {
        (41, "mt_dhcp"), (42, "mt_dhcp"), (43, "mt_dhcp"), (60, "fg_dhcp"), (61, "fg_dhcp")
    }  # fmt: skip
    sessions = {s.sessionid: s for o in site for s in o.sessions}
    kinds: Counter[str] = Counter()
    for kind, _, outcome in parsed(site):
        values = outcome.rows[0].values
        kinds[kind] += 1
        if kind == "arp":  # the core router has no ARP entry of the Wi-Fi VLANs
            assert ipaddress.ip_address(values["ip"]) not in ipaddress.ip_network("172.20.60.0/23")
        elif kind == "fg-dhcp.ack":
            assert values["interface"] in ("vlan60", "vlan61")
        elif kind == "traffic":
            session = sessions[int(values["sessionid"])]
            wifi = session.vlan in lg.FORTIGATE_VLANS_BOTH
            assert values["srcintf"] == (f"vlan{session.vlan}" if wifi else "port2")
            assert values["srcmac"] == (session.mac if wifi else lg.ROUTER_MAC)
    assert kinds["dhcp.assigned"] > 0
    assert kinds["fg-dhcp.ack"] > 0
    assert kinds["arp"] > 0


def test_srcmac_on_a_mikrotik_site_is_the_routers_mac_on_many_ips() -> None:
    """Behind the core router the FortiGate sees one MAC (the router's) for every client: the
    shared case the srcmac guard has to catch. Only traffic lines carry it."""
    lg = loadgen()
    site = simulate_plan((FRIDAY,), srcmac=True)
    ips_by_mac: dict[str, set[str]] = {}
    for kind, _, outcome in parsed(site):
        values = outcome.rows[0].values
        if kind == "traffic":
            ips_by_mac.setdefault(values["srcmac"], set()).add(values["srcip"])
        elif outcome.rows[0].table in ("fg_web", "fg_app", "fg_dns"):
            assert "srcmac" not in values.get("extra", {})
    assert list(ips_by_mac) == [lg.ROUTER_MAC]
    assert len(ips_by_mac[lg.ROUTER_MAC]) > 10


def test_egress_shares_interfaces_and_truth() -> None:
    """``--egress``: each exit carries its share of a day's sessions (within ±2 %), the
    session's lines leave by its interface with its NAT address, NAT ports are unique per
    address while a session runs, the truth names the exit and ``waninfo`` lists the
    interfaces."""
    site = simulate_plan((FRIDAY,), users=40, intensity=30.0, egresses=egresses(*EXITS))
    (day,) = site
    exits = {e.name: e for e in egresses(*EXITS)}
    counts = Counter(session.egress for session in day.sessions)
    assert len(day.sessions) > 5000
    for name, egress in exits.items():
        assert abs(100 * counts[name] / len(day.sessions) - egress.share) <= 2, counts
    sessions = {s.sessionid: s for s in day.sessions}
    for kind, message, outcome in parsed(site):
        values = outcome.rows[0].values
        if kind in ("traffic", "webfilter", "app-ctrl"):
            session = sessions[int(values["sessionid"])]
            egress = exits[session.egress]
            wire = message.wire.decode()
            assert f'dstintf="{egress.dstintf}"' in wire, wire
            if kind == "traffic":
                assert values["transip"] == egress.transip == session.transip
                assert int(values["transport"]) == session.transport
        elif kind == "perf-stats":
            waninfo = values["extra"]["waninfo"]
            assert [part.split(",")[0] for part in waninfo.split(";") if part] == [
                "name=wan1",
                "name=wan2",
            ]
    for name in exits:
        running: list[Any] = []
        for session in (s for s in day.sessions if s.egress == name):
            assert not [
                other
                for other in running
                if other.transport == session.transport and other.end > session.start
            ]
            running.append(session)


def test_the_new_options_on_the_command_line() -> None:
    args = ("--users", "4", "--intensity", "4", "--day", FRIDAY.isoformat())
    exits = [option for spec in EXITS for option in ("--egress", spec)]
    done = _run_script(*args, "--dhcp-server", "fortigate", "--srcmac", *exits, "truth")
    assert done.returncode == 0, done.stderr
    records = [json.loads(line) for line in done.stdout.splitlines()]
    assert {r["evidence"] for r in records if r["type"] == "lease"} == {"fg_dhcp", "none"}
    assert {r["egress"] for r in records if r["type"] == "session"} == {"hat-1", "hat-2"}
    default = _run_script(*args, "truth")
    assert {r["evidence"] for r in map(json.loads, default.stdout.splitlines())
            if r["type"] == "lease"} == {"mt_dhcp"}  # fmt: skip

    short = _run_script(*args, "--egress", "hat-1:100.64.12.9:wan1:60", "stats")
    assert short.returncode == 2
    assert "must sum to 100, not 60" in short.stderr
    both = _run_script(*args, "--transip", "100.64.12.9", *exits, "stats")
    assert both.returncode == 2
    assert "--transip and --egress exclude each other" in both.stderr
    malformed = _run_script(*args, "--egress", "hat-1:100.64.12.9:60", "stats")
    assert malformed.returncode == 2
    assert "NAME:TRANSIP:DSTINTF:SHARE" in malformed.stderr
    assert "Traceback" not in short.stderr + both.stderr + malformed.stderr


def test_spool_files_are_written_as_the_collector_writes_them(tmp_path: Path) -> None:
    data = tmp_path / "data"
    done = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--users",
            "3",
            "--intensity",
            "4",
            "--day",
            FRIDAY.isoformat(),
            "--only",
            "fortigate",
            "--only",
            "core-r1",
            "spool",
            "--data-dir",
            str(data),
            "--source-address",
            "core-r1=127.0.0.2",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    layout = Layout(data)
    router = layout.spool_file("core-r1", FRIDAY).read_bytes().splitlines(keepends=True)
    records = [parse_line(line) for line in router]
    assert {record.sender for record in records} == {"127.0.0.2"}
    assert [r.received for r in records] == sorted(r.received for r in records)
    assert router[0] == format_line(records[0].received, "127.0.0.2", records[0].message)
    assert layout.spool_file("fortigate", FRIDAY).exists()
    assert not (layout.spool / "switch-1").exists()
    again = subprocess.run(
        [sys.executable, str(SCRIPT), "--users", "3", "--day", FRIDAY.isoformat(), "spool",
         "--data-dir", str(data)],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert again.returncode == 1
    assert "exists" in again.stderr


@pytest.fixture
def collector_config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "127.0.0.1"},
                {"name": "core-r1", "type": "mikrotik", "address": "127.0.0.2"},
            ],
            "collector": {
                "bind": "127.0.0.1",
                "udp_port": 0,
                "tcp_port": 0,
                "fsync_interval_seconds": 0.02,
            },
        }
    )


def loopback_alias_works() -> bool:
    """127.0.0.2 is a loopback address on Linux and Windows (not on macOS by default)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(("127.0.0.2", 0))
        except OSError:
            return False
    return True


@pytest.fixture
def running(collector_config: Config) -> Iterator[tuple[Collector, asyncio.AbstractEventLoop]]:
    loop = asyncio.new_event_loop()
    now = datetime.combine(FRIDAY, datetime.min.time(), tzinfo=SITE_TZ) + timedelta(hours=10)
    collector = Collector(collector_config, clock=lambda: now.astimezone(UTC))
    loop.run_until_complete(collector.start())
    yield collector, loop
    loop.run_until_complete(collector.stop())
    loop.close()


def test_send_reaches_the_collector_over_udp_and_tcp(
    running: tuple[Collector, asyncio.AbstractEventLoop], collector_config: Config
) -> None:
    """``split``: the FortiGate over TCP with octet counting, CORE-R1 over UDP from its own
    loopback address; the collector stores exactly the bytes that were sent."""
    if not loopback_alias_works():  # pragma: no cover - macOS; CI runs Linux and Windows
        pytest.skip("127.0.0.2 is not a loopback address here")
    collector, loop = running
    lg = loadgen()
    settings = lg.Settings(seed=3, users=2, intensity=6.0)
    (output,) = lg.Simulation(settings).run(FRIDAY)
    messages = [m for m in output.messages if m.source in ("fortigate", "core-r1")][:400]
    udp, tcp = collector.udp_address, collector.tcp_address
    assert udp is not None
    assert tcp is not None
    sender = lg.Sender(
        "127.0.0.1",
        udp_port=udp[1],
        tcp_port=tcp[1],
        protocol="split",
        sources=settings.sources,
        bind={"fortigate": "127.0.0.1", "core-r1": "127.0.0.2"},
    )
    with sender:
        sent = lg.send_messages(messages, sender, rate=0)
        assert sent == len(messages)

        async def wait() -> None:
            for _ in range(2000):
                total = sum(s.messages for s in collector.stats.sources.values())
                if total >= len(messages):
                    return
                await asyncio.sleep(0.005)

        loop.run_until_complete(wait())
    layout = Layout(collector_config.data_dir)
    for source in ("fortigate", "core-r1"):
        stored = [
            parse_line(line).message
            for line in layout.spool_file(source, FRIDAY).read_bytes().splitlines()
        ]
        expected = [m.wire for m in messages if m.source == source]
        # UDP may reorder in theory; on loopback it does not, TCP never does.
        assert sorted(stored) == sorted(expected)
        if source == "fortigate":
            assert stored == expected
    assert lg.frame(b"<30>x") == b"5 <30>x"
