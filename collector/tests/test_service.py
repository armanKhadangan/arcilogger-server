"""Collector end to end over localhost UDP and TCP."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_collector.cli import main as cli_main
from arcilogger_collector.service import RECOVERED_AFTER, Collector, run, unknown_source_name
from arcilogger_core.alerts import read_alerts
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import parse_line

FORTIGATE_LINE = (
    b'<189>date=2026-09-23 time=09:44:57 devname="FIREWALL-01" logid="0000000013" '
    b'type="traffic" srcip=172.20.42.76 transip=100.64.12.9 transport=52814'
)


def _config(tmp_path: Path, *, known: bool = True, unknown: str = "write") -> Config:
    sources = [{"name": "local", "type": "fortigate", "address": "127.0.0.1"}] if known else []
    return Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "secrets_dir": str(tmp_path),
            "sources": sources,
            "collector": {
                "bind": "127.0.0.1",
                "udp_port": 0,
                "tcp_port": 0,
                "unknown_senders": unknown,
                "fsync_interval_seconds": 0.02,
                "status_interval_seconds": 0.02,
            },
        }
    )


async def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def _with_collector(
    config: Config, body: Callable[[Collector], Awaitable[None]], clock: Any = None
) -> None:
    async def scenario() -> None:
        collector = Collector(config) if clock is None else Collector(config, clock=clock)
        await collector.start()
        try:
            await body(collector)
        finally:
            await collector.stop()

    asyncio.run(scenario())


def _spool_messages(tmp_path: Path, source: str) -> list[bytes]:
    out: list[bytes] = []
    for file in sorted((tmp_path / "spool" / source).glob("*.log")):
        out += [parse_line(line).message for line in file.read_bytes().splitlines()]
    return out


def test_udp_message_is_written_unchanged(tmp_path: Path) -> None:
    async def body(collector: Collector) -> None:
        address = collector.udp_address
        assert address is not None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(FORTIGATE_LINE, address)
            sock.sendto(b"<30>multi\nline\n", address)
        await _wait_for(lambda: len(_spool_messages(tmp_path, "local")) == 2)

    _with_collector(_config(tmp_path), body)
    assert _spool_messages(tmp_path, "local") == [FORTIGATE_LINE, b"<30>multi\nline\n"]


def test_tcp_octet_counting_and_lf_framing(tmp_path: Path) -> None:
    async def body(collector: Collector) -> None:
        address = collector.tcp_address
        assert address is not None
        _reader, writer = await asyncio.open_connection(*address)
        writer.write(str(len(FORTIGATE_LINE)).encode() + b" " + FORTIGATE_LINE)
        writer.write(b"<30>2026-09-23T09:38:26+03:00 CORE-R1 dhcp,info dhcp60 assigned\n<30>par")
        await writer.drain()
        writer.write(b"tial\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await _wait_for(lambda: len(_spool_messages(tmp_path, "local")) == 3)

    _with_collector(_config(tmp_path), body)
    assert _spool_messages(tmp_path, "local") == [
        FORTIGATE_LINE,
        b"<30>2026-09-23T09:38:26+03:00 CORE-R1 dhcp,info dhcp60 assigned",
        b"<30>partial",
    ]


def test_unknown_sender_is_written_and_alerted_once(tmp_path: Path) -> None:
    async def body(collector: Collector) -> None:
        address = collector.udp_address
        assert address is not None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _ in range(3):
                sock.sendto(b"<14>hello", address)
        await _wait_for(lambda: len(_spool_messages(tmp_path, "unknown-127.0.0.1")) == 3)

    _with_collector(_config(tmp_path, known=False), body)
    alerts = read_alerts(Layout(tmp_path).alerts)
    assert [a["rule"] for a in alerts] == ["new-log-source"]
    assert alerts[0]["details"] == {"address": "127.0.0.1", "written": True}


def test_unknown_sender_can_be_dropped(tmp_path: Path) -> None:
    async def body(collector: Collector) -> None:
        address = collector.udp_address
        assert address is not None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"<14>hello", address)
            sock.sendto(b"<14>again", address)
        await _wait_for(lambda: collector.stats.dropped_unknown.get("127.0.0.1") == 2)

    _with_collector(_config(tmp_path, known=False, unknown="drop"), body)
    assert not (tmp_path / "spool").exists() or not any((tmp_path / "spool").iterdir())
    assert read_alerts(Layout(tmp_path).alerts)[0]["details"]["written"] is False


def test_midnight_switches_files_and_closes_the_old_day(tmp_path: Path) -> None:
    times = iter(
        [
            datetime(2026, 10, 12, 20, 59, 59, tzinfo=UTC),  # constructor (started_at)
            datetime(2026, 10, 12, 20, 59, 59, 500000, tzinfo=UTC),  # first message, 23:59:59.5
            datetime(2026, 10, 12, 21, 0, 0, 100000, tzinfo=UTC),  # second message, 00:00:00.1
        ]
    )
    last = [datetime(2026, 10, 12, 21, 0, 1, tzinfo=UTC)]

    def clock() -> datetime:
        value = next(times, None)
        if value is None:
            last[0] += timedelta(milliseconds=1)
            return last[0]
        return value

    async def body(collector: Collector) -> None:
        collector.handle_message("127.0.0.1", b"<1>before")
        collector.handle_message("127.0.0.1", b"<1>after")
        collector.maintain_once()
        assert collector._writer.open_files == [("local", date(2026, 10, 13))]

    _with_collector(_config(tmp_path), body, clock=clock)
    layout = Layout(tmp_path)
    assert layout.spool_file("local", date(2026, 10, 12)).is_file()
    assert layout.spool_file("local", date(2026, 10, 13)).is_file()


def test_status_file_reports_sources(tmp_path: Path) -> None:
    async def body(collector: Collector) -> None:
        collector.handle_message("127.0.0.1", b"<1>x")
        await _wait_for(lambda: Layout(tmp_path).collector_status.exists())

    _with_collector(_config(tmp_path), body)
    status = json.loads(Layout(tmp_path).collector_status.read_text(encoding="utf-8"))
    assert status["sources"]["local"]["messages"] == 1
    assert status["sources"]["local"]["last_received"].endswith("+03:00")


def test_run_stops_on_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(run(_config(tmp_path), stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
    assert Layout(tmp_path).collector_status.exists()


def test_unknown_source_names_are_file_safe() -> None:
    assert unknown_source_name("172.20.19.9") == "unknown-172.20.19.9"
    assert unknown_source_name("fe80::1%eth0") == "unknown-fe80--1-eth0"


def test_cli_without_command_prints_help() -> None:
    assert cli_main([]) == 2


def _transport_config(tmp_path: Path, transport: str) -> Config:
    config = _config(tmp_path)
    source = config.sources[0].model_copy(update={"transport": transport})
    return config.model_copy(update={"sources": (source,)})


def test_a_tcp_only_source_drops_forged_udp(tmp_path: Path) -> None:
    async def body(collector: Collector) -> None:
        udp, tcp = collector.udp_address, collector.tcp_address
        assert udp is not None
        assert tcp is not None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"<30>forged assigned", udp)
            sock.sendto(b"<30>forged again", udp)
        await _wait_for(lambda: collector.stats.dropped_transport.get("127.0.0.1") == 2)
        _reader, writer = await asyncio.open_connection(*tcp)
        writer.write(b"<30>real\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await _wait_for(lambda: len(_spool_messages(tmp_path, "local")) == 1)
        collector.write_status()

    _with_collector(_transport_config(tmp_path, "tcp"), body)
    assert _spool_messages(tmp_path, "local") == [b"<30>real"]
    alerts = read_alerts(Layout(tmp_path).alerts)
    assert [a["rule"] for a in alerts] == ["wrong-transport"]
    assert alerts[0]["severity"] == "high"
    assert alerts[0]["details"] == {
        "address": "127.0.0.1",
        "source": "local",
        "allowed": "tcp",
        "transport": "udp",
    }
    status = json.loads(Layout(tmp_path).collector_status.read_text(encoding="utf-8"))
    assert status["dropped_transport"] == {"127.0.0.1": 2}
    assert status["sources"]["local"]["messages"] == 1


def test_a_udp_only_source_drops_tcp_and_any_accepts_both(tmp_path: Path) -> None:
    udp_only = Collector(_transport_config(tmp_path / "u", "udp"))
    udp_only.handle_message("127.0.0.1", b"<1>tcp", "tcp")
    udp_only.handle_message("127.0.0.1", b"<1>udp", "udp")
    udp_only._writer.close()
    assert udp_only.stats.dropped_transport == {"127.0.0.1": 1}
    assert _spool_messages(tmp_path / "u", "local") == [b"<1>udp"]
    both = Collector(_transport_config(tmp_path / "a", "any"))
    both.handle_message("127.0.0.1", b"<1>tcp", "tcp")
    both.handle_message("127.0.0.1", b"<1>udp", "udp")
    both._writer.close()
    assert both.stats.dropped_transport == {}
    assert _spool_messages(tmp_path / "a", "local") == [b"<1>tcp", b"<1>udp"]


def _failing_writes(collector: Collector, failures: int) -> list[int]:
    """Make the next ``failures`` spool writes fail like a full disk; returns a call counter."""
    real = collector._writer.write
    calls = [0]

    def write(source: str, received: datetime, sender: str, message: bytes) -> None:
        calls[0] += 1
        if calls[0] <= failures:
            raise OSError(28, "No space left on device")
        real(source, received, sender, message)

    collector._writer.write = write  # type: ignore[method-assign]
    return calls


def test_spool_write_failures_are_counted_alerted_and_survived_over_tcp(tmp_path: Path) -> None:
    offset = [timedelta(0)]

    def clock() -> datetime:
        return datetime.now(UTC) + offset[0]

    async def body(collector: Collector) -> None:
        tcp = collector.tcp_address
        assert tcp is not None
        _failing_writes(collector, 3)
        # This test is about the write that ends the incident: the periodic check (a quiet
        # source, tested below) could close it first once the clock jumps, before that write.
        collector.check_recoveries = lambda now: None  # type: ignore[method-assign]
        _reader, writer = await asyncio.open_connection(*tcp)
        for number in range(5):
            writer.write(b"<30>message %d\n" % number)
            await writer.drain()
            await asyncio.sleep(0.01)
        # The connection stays open after the failures: later frames are written.
        await _wait_for(lambda: len(_spool_messages(tmp_path, "local")) == 2)
        # Good writes right after the failures are still part of the incident ...
        assert collector.stats.write_error() is not None
        # ... which ends with the first good write a minute after the last failure.
        offset[0] = RECOVERED_AFTER
        writer.write(b"<30>still connected\n")
        await writer.drain()
        await _wait_for(lambda: len(_spool_messages(tmp_path, "local")) == 3)
        writer.close()
        await writer.wait_closed()
        collector.write_status()

    _with_collector(_config(tmp_path), body, clock=clock)
    assert _spool_messages(tmp_path, "local") == [
        b"<30>message 3",
        b"<30>message 4",
        b"<30>still connected",
    ]
    alerts = [a for a in read_alerts(Layout(tmp_path).alerts) if a["rule"] == "R20-spool"]
    assert len(alerts) == 2  # the first failure (when the disk lets it) and the recovery
    assert alerts[0]["severity"] == "critical"
    assert alerts[1]["details"]["messages"] == 3
    assert alerts[1]["details"]["written_meanwhile"] == 3
    assert alerts[1]["details"]["source"] == "local"
    assert "local kaynağından 3 mesaj kaydedilemedi" in alerts[1]["message"]
    status = json.loads(Layout(tmp_path).collector_status.read_text(encoding="utf-8"))
    assert status["write_failures"] == {"local": 3}
    assert status["spool_write_error"] is None
    assert status["sources"]["local"]["messages"] == 3  # failed messages are not counted


def test_an_ongoing_write_failure_is_in_the_status_and_logged_once_a_minute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    now = [datetime(2026, 10, 12, 9, 0, tzinfo=UTC)]

    def clock() -> datetime:
        now[0] += timedelta(seconds=1)
        return now[0]

    collector = Collector(_config(tmp_path), clock=clock)
    _failing_writes(collector, 100)
    with caplog.at_level(logging.ERROR, logger="arcilogger.collector"):
        for _ in range(90):
            collector.handle_message("127.0.0.1", b"<1>x", "udp")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 2  # the first failure and one reminder a minute later
    status = collector.stats.as_json()
    assert status["write_failures"] == {"local": 90}
    assert status["spool_write_error"]["messages"] == 90
    assert status["spool_write_error"]["since"] == "2026-10-12T12:00:02.000000+03:00"
    assert status["spool_write_error"]["sources"] == ["local"]
    assert status["spool_write_errors"]["local"]["messages"] == 90
    assert status["sources"] == {}


def _source_config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "secrets_dir": str(tmp_path),
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "10.0.0.1"},
                {"name": "core-r1", "type": "mikrotik", "address": "10.0.0.2"},
            ],
            "collector": {"bind": "127.0.0.1", "udp_port": 0, "tcp_port": 0},
        }
    )


def _ticking_clock(
    step: timedelta = timedelta(seconds=1),
) -> tuple[Callable[[], datetime], list[datetime]]:
    now = [datetime(2026, 10, 12, 9, 0, tzinfo=UTC)]

    def clock() -> datetime:
        now[0] += step
        return now[0]

    return clock, now


def test_one_failing_source_is_one_incident_while_another_source_is_written(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The file of one source cannot be written (a sharing error, or its long lines no longer
    # fit on a nearly full disk), the other source is written normally in between.
    clock, now = _ticking_clock()
    collector = Collector(_source_config(tmp_path), clock=clock)
    real = collector._writer.write
    failing = [True]

    def write(source: str, received: datetime, sender: str, message: bytes) -> None:
        if source == "fortigate" and failing[0]:
            raise PermissionError(13, "Access is denied")
        real(source, received, sender, message)

    collector._writer.write = write  # type: ignore[method-assign]
    layout = Layout(tmp_path)
    with caplog.at_level(logging.WARNING, logger="arcilogger.collector"):
        for number in range(100):
            collector.handle_message("10.0.0.1", b"<1>fg %d" % number, "udp")
            collector.handle_message("10.0.0.2", b"<1>core %d" % number, "udp")
        spool_alerts = [a for a in read_alerts(layout.alerts) if a["rule"] == "R20-spool"]
        assert len(spool_alerts) == 1
        assert spool_alerts[0]["details"]["source"] == "fortigate"
        status = collector.stats.as_json()
        assert status["write_failures"] == {"fortigate": 100}
        assert status["spool_write_error"]["sources"] == ["fortigate"]
        assert status["spool_write_error"]["messages"] == 100
        assert set(status["spool_write_errors"]) == {"fortigate"}
        assert status["sources"]["core-r1"]["messages"] == 100
        # 200 messages over 200 s: the first error and one reminder a minute, nothing else.
        assert [r.levelno for r in caplog.records] == [logging.ERROR] * 4
        failing[0] = False
        collector.handle_message("10.0.0.1", b"<1>fg back", "udp")  # 1 s after: same incident
        assert collector.stats.write_error() is not None
        now[0] += RECOVERED_AFTER
        collector.handle_message("10.0.0.1", b"<1>fg back again", "udp")
    spool_alerts = [a for a in read_alerts(layout.alerts) if a["rule"] == "R20-spool"]
    assert len(spool_alerts) == 2
    assert spool_alerts[1]["details"]["messages"] == 100
    assert spool_alerts[1]["details"]["written_meanwhile"] == 2
    assert collector.stats.as_json()["spool_write_error"] is None
    assert collector.stats.as_json()["spool_write_errors"] == {}
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    collector._writer.close()
    assert _spool_messages(tmp_path, "fortigate") == [b"<1>fg back", b"<1>fg back again"]


def test_a_source_whose_writes_alternate_stays_one_incident(tmp_path: Path) -> None:
    # A nearly full disk: every other line of the same source fits, the next one does not.
    clock, _now = _ticking_clock()
    collector = Collector(_source_config(tmp_path), clock=clock)
    real = collector._writer.write
    calls = [0]

    def write(source: str, received: datetime, sender: str, message: bytes) -> None:
        calls[0] += 1
        if calls[0] % 2 and calls[0] < 120:
            raise OSError(28, "No space left on device")
        real(source, received, sender, message)

    collector._writer.write = write  # type: ignore[method-assign]
    for number in range(200):  # one a second: failures until 119 s, then only good writes
        collector.handle_message("10.0.0.1", b"<1>fg %d" % number, "udp")
    spool_alerts = [a for a in read_alerts(Layout(tmp_path).alerts) if a["rule"] == "R20-spool"]
    assert len(spool_alerts) == 2  # the first failure and, a minute after the last, the recovery
    assert spool_alerts[1]["details"]["messages"] == 60
    assert collector.stats.write_failures == {"fortigate": 60}
    assert collector.stats.write_error() is None
    recovered = datetime.fromisoformat(spool_alerts[1]["details"]["recovered"])
    assert recovered - datetime.fromisoformat(spool_alerts[1]["details"]["last"]) == RECOVERED_AFTER


def test_a_quiet_source_is_closed_by_the_maintenance(tmp_path: Path) -> None:
    clock, now = _ticking_clock()
    collector = Collector(_source_config(tmp_path), clock=clock)
    _failing_writes(collector, 1)
    collector.handle_message("10.0.0.2", b"<1>lost", "udp")
    collector.handle_message("10.0.0.2", b"<1>kept", "udp")  # 1 s later: incident still open
    collector.maintain_once()
    assert collector.stats.write_error() is not None
    now[0] += RECOVERED_AFTER
    collector.maintain_once()  # no message since, but written after the failure: recovered
    assert collector.stats.write_error() is None
    spool_alerts = [a for a in read_alerts(Layout(tmp_path).alerts) if a["rule"] == "R20-spool"]
    assert [a["details"].get("recovered") is not None for a in spool_alerts] == [False, True]
    collector._writer.close()
    # A source that only failed (never written since) stays failing until it is written.
    other = Collector(_source_config(tmp_path / "o"), clock=clock)
    _failing_writes(other, 5)
    other.handle_message("10.0.0.1", b"<1>lost", "udp")
    now[0] += RECOVERED_AFTER * 5
    other.maintain_once()
    assert other.stats.write_error() is not None
    other._writer.close()


def test_alerts_that_cannot_be_written_do_not_break_the_receive_path(tmp_path: Path) -> None:
    collector = Collector(_config(tmp_path))
    _failing_writes(collector, 1)
    Layout(tmp_path).alerts.mkdir(parents=True)  # a directory in the way: alerts fail
    collector.handle_message("127.0.0.1", b"<1>lost", "udp")
    collector.handle_message("127.0.0.1", b"<1>kept", "udp")
    collector._writer.close()
    assert collector.stats.write_failures == {"local": 1}
    assert _spool_messages(tmp_path, "local") == [b"<1>kept"]


def test_start_terminates_a_torn_last_line_of_today(tmp_path: Path) -> None:
    today = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    layout = Layout(tmp_path)
    path = layout.spool_file("local", date(2026, 10, 12))
    path.parent.mkdir(parents=True)
    fragment = b"2026-10-12T11:59:59.000000+03:00 127.0.0.1 <30>dhcp43 assig"
    path.write_bytes(fragment)

    async def body(collector: Collector) -> None:
        assert path.read_bytes() == fragment + b"\n"
        collector.handle_message("127.0.0.1", b"<1>next", "udp")
        collector.write_status()

    _with_collector(_config(tmp_path), body, clock=lambda: today)
    lines = path.read_bytes().splitlines()
    assert lines[0] == fragment
    assert parse_line(lines[1]).message == b"<1>next"
    alerts = read_alerts(layout.alerts)
    assert [a["rule"] for a in alerts] == ["R20-spool-torn"]
    assert alerts[0]["details"] == {"source": "local", "day": "2026-10-12", "offset": 59}
    status = json.loads(layout.collector_status.read_text(encoding="utf-8"))
    assert status["torn_lines_total"] == 1
    assert status["torn_lines"][0]["source"] == "local"
    assert status["torn_lines"][0]["offset"] == len(fragment)
