"""Load generator attack scenarios (Wave 6 contract §2.7, T-I).

Each scenario's lines (kind ``scenario.<name>``) go through the real parsers, and the rows are
checked against the match of the rule they are written for, with the rule's default parameters
(contract §2.2): the table, the fields the rule reads, the count within the window. On both
kinds of site: the MikroTik site (the default) and the FortiGate-only site. The rule modules of
W6-B, W6-D and W6-E are not on ``wave-6`` yet; when they are, this test also runs each rule on
the rows (the W6-I documentation pull request).
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import itertools
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.timeutil import to_site
from arcilogger_ingest.parsers import LinePosition, SourceType, parse_line

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "loadgen" / "arcilogger_loadgen.py"
#: A Wednesday (work hours 08:00 to 18:00 by default).
DAY = date(2026, 10, 7)
INTERNAL = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


@cache
def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


Row = tuple[str, dict[str, Any]]


@cache
def day_output(scenarios: tuple[str, ...], dhcp_server: str) -> Any:
    lg = loadgen()
    settings = lg.Settings(users=8, intensity=6.0, dhcp_server=dhcp_server, scenarios=scenarios)
    return next(lg.Simulation(settings).run(DAY))


def rows_of(messages: Iterable[Any]) -> list[Row]:
    """The typed rows of ``messages`` through the real parsers (no parse error allowed)."""
    found = []
    for k, message in enumerate(messages, 1):
        kind: SourceType = "fortigate" if message.source == "fortigate" else "mikrotik"
        outcome = parse_line(
            RawRecord(message.received, "172.20.10.1", message.wire),
            LinePosition(message.source, kind, DAY, k, 0),
        )
        assert outcome.error is None, (message.wire, outcome.error, outcome.detail)
        found += [(row.table, dict(row.values)) for row in outcome.rows]
    return found


def scenario_rows(name: str, dhcp_server: str) -> list[Row]:
    out = day_output((name,), dhcp_server)
    return rows_of(m for m in out.messages if m.kind == f"scenario.{name}")


def internal(ip: object) -> bool:
    return ip is not None and any(ipaddress.ip_address(str(ip)) in net for net in INTERNAL)


def most_in_window(rows: list[dict[str, Any]], seconds: int, key: Callable[[Any], object]) -> int:
    """The largest number of distinct ``key`` values among rows within ``seconds`` of each
    other (receive time)."""
    ordered = sorted(rows, key=lambda r: r["time_received"])
    best = 0
    for k, first in enumerate(ordered):
        end = first["time_received"] + timedelta(seconds=seconds)
        inside = [r for r in ordered[k:] if r["time_received"] <= end]
        best = max(best, len({key(r) for r in inside}))
    return best


def table(rows: list[Row], name: str) -> list[dict[str, Any]]:
    return [values for t, values in rows if t == name]


def local(moment: datetime) -> datetime:
    return to_site(moment)


# -- the match of each rule (contract §2.2, default parameters) -----------------------------------


def check_r01(rows: list[Row]) -> None:
    denied = [
        r for r in table(rows, "fg_traffic") if r["action"] == "deny" and internal(r["srcip"])
    ]
    assert most_in_window(denied, 60, lambda r: r["dstport"]) >= 30


def check_r02(rows: list[Row]) -> None:
    denied = [
        r for r in table(rows, "fg_traffic") if r["action"] == "deny" and not internal(r["srcip"])
    ]
    assert most_in_window(denied, 300, lambda r: r["id_"]) >= 100


def check_r03(rows: list[Row]) -> None:
    failed = [r for r in table(rows, "fg_event") if r["logid"] == "0100032002"]
    failed += [r for r in table(rows, "mt_account") if r["action"] == "login_failure"]
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in failed:
        by_user[row["user_name"]].append(row)
    assert max(most_in_window(found, 300, lambda r: r["id_"]) for found in by_user.values()) >= 5


def check_r04(rows: list[Row]) -> None:
    logins = [r for r in table(rows, "fg_event") if r["logid"] == "0100032001"]
    logins += [r for r in table(rows, "mt_account") if r["action"] == "login"]
    assert logins
    assert all(not 8 <= local(r["time"]).hour < 18 for r in logins)


def check_r05(rows: list[Row]) -> None:
    assert any(r["severity"] in {"high", "critical"} for r in table(rows, "fg_ips"))


def check_r06(rows: list[Row]) -> None:
    assert any(r["eventtype"] == "botnet" for r in table(rows, "fg_ips"))


def check_r07(rows: list[Row]) -> None:
    assert [r["virus"] for r in table(rows, "fg_virus")] == ["Synthetic.Test.File"]


def check_r08(rows: list[Row]) -> None:
    queries = [
        r
        for r in table(rows, "fg_traffic")
        if internal(r["srcip"]) and r["dstport"] in {53, 853} and not internal(r["dstip"])
    ]
    assert most_in_window(queries, 3600, lambda r: r["id_"]) >= 20


def check_r09(rows: list[Row]) -> None:
    blocked = [r for r in table(rows, "fg_web") if r["action"] == "blocked"]
    assert most_in_window(blocked, 3600, lambda r: r["id_"]) >= 20
    assert len({str(r["srcip"]) for r in blocked}) == 1


def check_r10(rows: list[Row]) -> None:
    assert any(r["app"] == "Tor" or r["appcat"] == "Proxy" for r in table(rows, "fg_app"))


def check_r11(rows: list[Row]) -> None:
    dhcp = table(rows, "mt_dhcp") + table(rows, "fg_dhcp")
    assert len(dhcp) == 1
    assert str(dhcp[0]["mac"]).startswith("00:00:5e:00:53:f")


def check_r12(rows: list[Row]) -> None:
    sightings = table(rows, "mt_arp") + table(rows, "mt_dhcp") + table(rows, "fg_dhcp")
    ordered = sorted(sightings, key=lambda r: r["time_received"])
    macs = [str(r["mac"]) for r in ordered]
    changes = sum(1 for a, b in itertools.pairwise(macs) if a != b)
    assert changes >= 3
    assert ordered[-1]["time_received"] - ordered[0]["time_received"] <= timedelta(hours=1)
    assert len({str(r["ip"]) for r in ordered}) == 1


def check_r13(rows: list[Row]) -> None:
    assert any(r["sentbyte"] > 2**30 for r in table(rows, "fg_traffic"))


def check_r14(rows: list[Row]) -> None:
    night = [r for r in table(rows, "fg_traffic") if local(r["time"]).hour < 6]
    assert night
    assert all(r["sentbyte"] >= 10 * 2**20 and r["rcvdbyte"] >= 10 * 2**20 for r in night)


def check_r16(rows: list[Row]) -> None:
    changes = [r for r in table(rows, "fg_event") if r["logid"] in {"0100044546", "0100044547"}]
    changes += table(rows, "mt_config")
    assert changes
    assert all(r["user_name"] == "admin" for r in changes)


def check_r17(rows: list[Row]) -> None:
    assert any("conserve" in (r["logdesc"] or "").lower() for r in table(rows, "fg_event"))


def check_r18(rows: list[Row]) -> None:
    pool = [r for r in table(rows, "mt_event") if r["event"] == "dhcp_pool_empty"]
    pool += [r for r in table(rows, "fg_event") if "ran out of" in (r["logdesc"] or "")]
    assert len(pool) == 1


def check_r19(rows: list[Row]) -> None:
    nxdomain = [r for r in table(rows, "fg_dns") if r["extra"].get("rcode") in {"NXDOMAIN", "3"}]
    assert most_in_window(nxdomain, 3600, lambda r: r["id_"]) > 200


CHECKS: dict[str, Callable[[list[Row]], None]] = {
    "R01": check_r01,
    "R02": check_r02,
    "R03": check_r03,
    "R04": check_r04,
    "R05": check_r05,
    "R06": check_r06,
    "R07": check_r07,
    "R08": check_r08,
    "R09": check_r09,
    "R10": check_r10,
    "R11": check_r11,
    "R12": check_r12,
    "R13": check_r13,
    "R14": check_r14,
    "R16": check_r16,
    "R17": check_r17,
    "R18": check_r18,
    "R19": check_r19,
}


def _numbered(rows: list[Row]) -> list[Row]:
    """Give every row a unique ``id_`` (the parser gives none; a count needs one)."""
    return [(t, {**values, "id_": k}) for k, (t, values) in enumerate(rows)]


@pytest.mark.parametrize("dhcp_server", ["mikrotik", "fortigate"])
@pytest.mark.parametrize("name", list(loadgen().SCENARIOS))
def test_each_scenario_matches_its_rule(name: str, dhcp_server: str) -> None:
    rule = loadgen().SCENARIOS[name]
    rows = _numbered(scenario_rows(name, dhcp_server))
    if dhcp_server == "fortigate" and rule in {"R03", "R04", "R16"}:
        # A FortiGate-only site: the FortiGate part of the rule alone (no router lines).
        assert not table(rows, "mt_account")
        assert not table(rows, "mt_config")
    CHECKS[rule](rows)


def test_fw_health_keeps_the_cpu_busy_for_the_sustain_time() -> None:
    """R17: every performance sample of the busy half hour is above 80 % (V3: ``cpu`` in
    ``extra``), for longer than the default 600 s."""
    out = day_output(("fw-health",), "mikrotik")
    perf = [
        values
        for t, values in rows_of(m for m in out.messages if m.kind == "perf-stats")
        if t == "fg_event" and values["logid"] == "0100040704"
    ]
    busy = [r for r in perf if int(r["extra"]["cpu"]) > 80]
    assert len(busy) == len(loadgen().HOT_SLOTS)
    assert busy[-1]["time"] - busy[0]["time"] >= timedelta(seconds=600)
    quiet = day_output((), "mikrotik")
    assert all(
        int(values["extra"]["cpu"]) < 80
        for t, values in rows_of(m for m in quiet.messages if m.kind == "perf-stats")
        if t == "fg_event"
    )


def test_all_is_every_scenario_and_changes_nothing_else() -> None:
    lg = loadgen()
    assert lg.parse_scenarios("all") == tuple(lg.SCENARIOS)
    assert lg.parse_scenarios("mac-flap, brute-force") == ("brute-force", "mac-flap")
    with pytest.raises(argparse.ArgumentTypeError, match="unknown scenario: nope;"):
        lg.parse_scenarios("brute-force,nope")
    every = day_output(tuple(lg.SCENARIOS), "mikrotik")
    kinds = Counter(m.kind for m in every.messages)
    labelled = {k.removeprefix("scenario.") for k in kinds if k.startswith("scenario.")}
    assert labelled == set(lg.SCENARIOS)
    plain = day_output((), "mikrotik")
    normal = Counter(m.kind for m in plain.messages)
    assert {k: v for k, v in kinds.items() if not k.startswith("scenario.")} == normal


def test_cli_option() -> None:
    lg = loadgen()
    args = lg.build_parser().parse_args(["--scenario", "all", "stats"])
    assert args.scenario == tuple(lg.SCENARIOS)
    args = lg.build_parser().parse_args(["stats"])
    assert args.scenario == ()
