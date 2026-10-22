"""Replica, retention, clock check and credit check."""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.alerts import read_alerts
from arcilogger_core.chain import sealed_days, verify_range
from arcilogger_core.clockcheck import (
    NO_LOCAL_SOURCE,
    NTP_EPOCH_OFFSET,
    ClockError,
    NtpResult,
    check_clock,
    sntp_query,
)
from arcilogger_core.credit import check_credit
from arcilogger_core.immutability import unlock
from arcilogger_core.replica import replicate_day, replicate_due
from arcilogger_core.retention import run_retention
from arcilogger_core.tsa.base import TsaUnavailableError
from arcilogger_core.tsa.mock import MockFailure, MockTsaProvider


def _seal(world: Any, days: list[date]) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    assert all(o.status == "sealed" for o in world.sealer().seal_due())


# -- replica -------------------------------------------------------------------------------------


def test_replica_copies_and_verifies(world: Any, days: list[date], tmp_path: Path) -> None:
    _seal(world, days)
    target = tmp_path / "nas"
    outcomes = replicate_due(world.layout, target)
    assert [(o.status, o.copied) for o in outcomes] == [("copied", 5), ("copied", 5), ("copied", 5)]
    copy = target / "2026" / "10" / "11"
    assert sorted(p.name for p in copy.iterdir()) == sorted(
        p.name for p in world.layout.vault_day(days[1]).iterdir()
    )
    assert replicate_due(world.layout, target) == []  # all recorded
    assert replicate_day(world.layout, days[0], target).status == "up-to-date"


def test_replica_never_overwrites_a_different_file(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    _seal(world, days[:1])
    target = tmp_path / "nas"
    replicate_day(world.layout, days[0], target)
    victim = target / "2026" / "10" / "10" / "fortigate.log.gz"
    victim.write_bytes(b"tampered")
    outcome = replicate_day(world.layout, days[0], target)
    assert outcome.status == "failed"
    assert victim.read_bytes() == b"tampered"
    assert read_alerts(world.layout.alerts)[-1]["severity"] == "critical"


def test_replica_of_unsealed_day_or_unreachable_target(world: Any, tmp_path: Path) -> None:
    assert replicate_day(world.layout, date(2026, 1, 1), tmp_path).status == "failed"


def test_replica_target_error_is_alerted(world: Any, days: list[date], tmp_path: Path) -> None:
    _seal(world, days[:1])
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("file in the way")
    outcome = replicate_day(world.layout, days[0], blocker)
    assert outcome.status == "failed"
    assert read_alerts(world.layout.alerts)[-1]["rule"] == "R20-replica"


# -- retention -----------------------------------------------------------------------------------


def _old_world(world: Any, days: list[date]) -> list[date]:
    _seal(world, days)
    world.reconfigure(retention={"enabled": True, "days": 730})
    world.set_now(days[1] + timedelta(days=730))  # cutoff = days[1]: only days[0] is past retention
    return days


def test_retention_is_off_by_default(world: Any, days: list[date]) -> None:
    _seal(world, days)
    world.now += timedelta(days=2000)
    result = run_retention(
        world.config,
        provider=MockTsaProvider(world.tsa),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
    )
    assert result.enabled is False
    assert result.candidates == days
    assert sealed_days(world.layout) == days


def test_retention_deletes_with_a_sealed_record_and_keeps_the_chain(
    world: Any, days: list[date]
) -> None:
    # Two sealed days; the third fixture day is the "next day" sealed after the deletion (a day
    # that ended in the real past: the mock TSA's time must be after the end of a sealed day).
    following = days[2]
    days = days[:2]
    _old_world(world, days)
    result = run_retention(
        world.config,
        provider=MockTsaProvider(world.tsa),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
    )
    assert result.error is None, result.error
    assert result.deleted == [days[0]]
    layout = world.layout
    assert sealed_days(layout) == days[1:]
    record = json.loads((layout.deletions / result.record).read_bytes())
    assert record["schema"] == "arcilogger.deletion/1"
    assert record["days"][0]["day"] == "2026-10-10"
    assert "2 yıl" in record["reason"]
    anchor = json.loads(layout.chain_anchor.read_bytes())
    assert anchor["day"] == "2026-10-10"
    checks = verify_range(layout, days[1:], [world.tsa.root_certificate])
    assert all(c.ok for c in checks), [c.errors for c in checks]
    assert world.sealer().state(days[0])["status"] == "deleted"

    # The next manifest seals the deletion record into the chain.
    next_day = following
    world.busy_day(next_day)
    world.set_now(next_day + timedelta(days=1))
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
    manifest = json.loads(layout.manifest(next_day).read_bytes())
    assert manifest["deletions"][0]["path"] == f"deletions/{result.record}"


def test_retention_refuses_to_delete_unverifiable_days(world: Any, days: list[date]) -> None:
    _old_world(world, days)
    victim = world.layout.vault_day(days[0]) / "fortigate.log.gz"
    unlock(victim)
    victim.write_bytes(b"changed")
    result = run_retention(
        world.config,
        provider=MockTsaProvider(world.tsa),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
    )
    assert result.deleted == []
    assert result.error
    assert "does not verify" in result.error
    assert days[0] in sealed_days(world.layout)


def test_retention_dry_run_and_failed_stamp(world: Any, days: list[date]) -> None:
    _old_world(world, days)
    dry = run_retention(
        world.config,
        provider=MockTsaProvider(world.tsa),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
        dry_run=True,
    )
    assert dry.candidates == [days[0]]
    assert dry.deleted == []
    world.tsa.failure = MockFailure.EXPIRED_CERT
    failed = run_retention(
        world.config,
        provider=MockTsaProvider(world.tsa),
        roots=[world.tsa.root_certificate],
        clock=world.clock,
    )
    assert failed.deleted == []
    assert failed.error
    assert "did not verify" in failed.error


# -- clock ---------------------------------------------------------------------------------------


@pytest.fixture
def ntp_server() -> Iterator[tuple[str, int, dict[str, float]]]:
    """Tiny NTP server whose clock runs `skew` seconds ahead."""
    settings = {"skew": 2.0}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.2)
    stop = threading.Event()

    def ntp(seconds: float) -> bytes:
        return struct.pack(
            "!II", int(seconds) + NTP_EPOCH_OFFSET, int((seconds % 1) * 2**32) & 0xFFFFFFFF
        )

    def serve() -> None:
        while not stop.is_set():
            try:
                data, peer = sock.recvfrom(512)
            except OSError:
                continue
            now = time.time() + settings["skew"]
            reply = bytes([0x24, 2, 6, 0xEC]) + b"\x00" * 20 + data[40:48] + ntp(now) + ntp(now)
            sock.sendto(reply, peer)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield "127.0.0.1", sock.getsockname()[1], settings
    stop.set()
    thread.join()
    sock.close()


def test_sntp_measures_the_offset(ntp_server: tuple[str, int, dict[str, float]]) -> None:
    host, port, _ = ntp_server
    result = sntp_query(host, port, timeout=2)
    assert 1.9 < result.offset_s < 2.1
    assert result.stratum == 2


def test_sntp_timeout() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
        silent.bind(("127.0.0.1", 0))
        with pytest.raises(ClockError):
            sntp_query("127.0.0.1", silent.getsockname()[1], timeout=0.2)


def test_clock_check_records_samples_and_alerts(
    world: Any, ntp_server: tuple[str, int, dict[str, float]]
) -> None:
    host, port, settings = ntp_server
    world.reconfigure(clock={"ntp_server": host, "ntp_port": port, "fallback_server": host})
    sample = check_clock(world.config, clock=world.clock)
    assert sample["ok"] is True
    assert sample["server"] == host
    assert read_alerts(world.layout.alerts)[-1]["rule"] == "R20-clock"  # 2 s > 1 s
    settings["skew"] = 0.0
    check_clock(world.config, clock=world.clock)
    samples = world.layout.clock_samples(date(2026, 10, 13)).read_text().splitlines()
    assert len(samples) == 2
    assert abs(json.loads(samples[1])["offset_us"]) < 100_000


def test_clock_check_falls_back_and_reports_unreachable(world: Any) -> None:
    world.reconfigure(clock={"ntp_server": "172.20.10.1"})  # the example plan's core router
    calls: list[str] = []

    def query(server: str, port: int) -> NtpResult:
        calls.append(server)
        if server == "172.20.10.1":
            raise ClockError("timeout")
        return NtpResult(offset_s=0.003, delay_s=0.01, stratum=2)

    sample = check_clock(world.config, clock=world.clock, query=query)
    assert calls == ["172.20.10.1", "time.ume.tubitak.gov.tr"]
    assert sample["offset_us"] == 3000
    assert "warning" not in sample

    def down(server: str, port: int) -> NtpResult:
        raise ClockError(f"{server} down")

    assert check_clock(world.config, clock=world.clock, query=down)["ok"] is False
    assert "ulaşılamadı" in read_alerts(world.layout.alerts)[-1]["message"]


def test_clock_check_without_a_local_time_source_asks_only_the_fallback(world: Any) -> None:
    """``clock.ntp_server`` is empty by default (no customer address as a default): the check
    still works with the public fallback, and every sample says that no local source is set."""
    assert world.config.clock.ntp_server == ""
    calls: list[str] = []

    def query(server: str, port: int) -> NtpResult:
        calls.append(server)
        return NtpResult(offset_s=-0.002, delay_s=0.01, stratum=1)

    sample = check_clock(world.config, clock=world.clock, query=query)
    assert calls == ["time.ume.tubitak.gov.tr"]  # never "" (that would be this machine)
    assert (sample["ok"], sample["server"], sample["offset_us"]) == (
        True,
        "time.ume.tubitak.gov.tr",
        -2000,
    )
    assert sample["warning"] == NO_LOCAL_SOURCE
    assert "clock.ntp_server" in NO_LOCAL_SOURCE
    assert read_alerts(world.layout.alerts) == []  # a warning in the sample, not an alert
    stored = world.layout.clock_samples(date(2026, 10, 13)).read_text("utf-8").splitlines()
    assert json.loads(stored[-1])["warning"] == NO_LOCAL_SOURCE


def test_clock_check_without_any_time_server_reports_it(world: Any) -> None:
    world.reconfigure(clock={"ntp_server": " ", "fallback_server": ""})
    assert world.config.clock.servers == ()

    def query(server: str, port: int) -> NtpResult:  # pragma: no cover - must not be called
        raise AssertionError(server)

    sample = check_clock(world.config, clock=world.clock, query=query)
    assert (sample["ok"], sample["server"]) == (False, None)
    assert "no time server is configured" in str(sample["error"])
    (alert,) = read_alerts(world.layout.alerts)
    assert (alert["rule"], alert["severity"]) == ("R20-clock", "high")
    assert "no time server is configured" in alert["message"]


# -- credit --------------------------------------------------------------------------------------


def test_credit_check_logs_and_alerts_below_threshold(world: Any) -> None:
    provider = MockTsaProvider(world.tsa)
    world.tsa.credits = 9634
    assert check_credit(provider, world.layout, 500, clock=world.clock) == 9634
    world.tsa.credits = 480
    assert check_credit(provider, world.layout, 500, clock=world.clock) == 480
    assert "480" in read_alerts(world.layout.alerts)[-1]["message"]
    lines = world.layout.credit_log.read_text().splitlines()
    assert [json.loads(line)["credits"] for line in lines] == [9634, 480]


def test_credit_check_failure_is_logged(world: Any) -> None:
    class Broken(MockTsaProvider):
        def credits(self) -> int | None:
            raise TsaUnavailableError("no route")

    assert check_credit(Broken(world.tsa), world.layout, 500, clock=world.clock) is None
    assert read_alerts(world.layout.alerts)[-1]["rule"] == "R20-credit"
