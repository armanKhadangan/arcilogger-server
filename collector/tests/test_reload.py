"""Live reload of ``[[sources]]`` in the collector, with no restart (ADR-0014; contract 2.8).

A sender on 127.0.0.2 stands for a new device: the collector, bound to 127.0.0.1, sees its
datagrams with that source address (Linux routes the whole of 127.0.0.0/8 to the loopback).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from arcilogger_collector.service import Collector, run
from arcilogger_core.config import load_config
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import parse_line

TEXT = """data_dir = "{data}"
site_name = "Örnek A.Ş."

[collector]
bind = "127.0.0.1"
udp_port = 0
tcp_port = 0
fsync_interval_seconds = 0.02
status_interval_seconds = 30

[[sources]]
name = "core-r1"
type = "mikrotik"
address = "127.0.0.1"
"""
SWITCH = '\n[[sources]]\nname = "switch-2"\ntype = "mikrotik"\naddress = "127.0.0.2"\n'
MESSAGE = b"<30>2026-10-25T09:38:26.380+03:00 SWITCH-2 system,info link up"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "arcilogger.toml"
    path.write_text(TEXT.format(data=(tmp_path / "data").as_posix()), encoding="utf-8")
    return path


@pytest.fixture
def sender() -> Iterator[socket.socket]:
    """A UDP socket on 127.0.0.2, the new device."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.2", 0))
    except OSError as exc:
        sock.close()
        pytest.skip(f"127.0.0.2 is not a local address here: {exc}")
    yield sock
    sock.close()


def rewrite(path: Path, text: str) -> None:
    """New content with a new modification time (as the agent's replace or an editor)."""
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def spooled(layout: Layout, source: str) -> list[bytes]:
    return [
        parse_line(line).message
        for file in sorted((layout.spool / source).glob("*.log"))
        for line in file.read_bytes().splitlines()
    ]


def status(layout: Layout) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(layout.collector_status.read_bytes())
    return data


async def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def with_collector(config_path: Path, body: Callable[[Collector], Awaitable[None]]) -> None:
    async def scenario() -> None:
        collector = Collector(
            load_config(config_path), config_path=config_path, reload_seconds=0.05
        )
        await collector.start()
        try:
            await body(collector)
        finally:
            await collector.stop()

    asyncio.run(scenario())


async def send(collector: Collector, sock: socket.socket, count: int, start: int = 0) -> None:
    address = collector.udp_address
    assert address is not None
    for index in range(start, start + count):
        sock.sendto(MESSAGE + f" #{index}".encode(), address)
        await asyncio.sleep(0.002)


def test_a_new_source_is_taken_while_its_datagrams_arrive(
    config_path: Path, sender: socket.socket
) -> None:
    layout = Layout(load_config(config_path).data_dir)

    async def body(collector: Collector) -> None:
        listening = collector.udp_address
        await wait_for(lambda: layout.collector_status.exists())
        assert status(layout)["config"]["sha256"] == sha(config_path)
        sending = asyncio.create_task(send(collector, sender, 400))
        await asyncio.sleep(0.2)  # datagrams of a sender the configuration does not know yet
        rewrite(config_path, config_path.read_text(encoding="utf-8") + SWITCH)
        await wait_for(lambda: collector.config.source_for("127.0.0.2") is not None)
        await wait_for(lambda: status(layout)["config"]["sha256"] == sha(config_path))
        await sending
        total = 400
        await wait_for(lambda: collector.stats.sources.get("switch-2") is not None)
        await wait_for(
            lambda: sum(stats.messages for stats in collector.stats.sources.values()) == total
        )
        assert collector.udp_address == listening  # the same socket: nothing was restarted

    with_collector(config_path, body)
    unknown = spooled(layout, "unknown-127.0.0.2")
    switch = spooled(layout, "switch-2")
    assert unknown, "the sender was unknown before the reload"
    assert switch, "and a source of its own after it"
    numbers = [int(message.rsplit(b"#", 1)[1]) for message in unknown + switch]
    assert numbers == list(range(400)), "none lost, none twice, in order"
    block = status(layout)["config"]
    assert (block["sha256"], block["error"], block["restart_keys"]) == (sha(config_path), None, [])


def test_a_broken_file_keeps_the_sources_and_messages_keep_arriving(
    config_path: Path, sender: socket.socket
) -> None:
    good = config_path.read_text(encoding="utf-8") + SWITCH
    rewrite(config_path, good)
    layout = Layout(load_config(config_path).data_dir)
    good_sha = sha(config_path)

    async def body(collector: Collector) -> None:
        await wait_for(lambda: layout.collector_status.exists())
        rewrite(config_path, good.replace('address = "127.0.0.2"', 'address = "127.0.0.2'))
        await wait_for(lambda: status(layout)["config"]["error"] is not None)
        block = status(layout)["config"]
        assert block["sha256"] == good_sha  # still the file the sources came from
        assert "TOMLDecodeError" in block["error"]
        await send(collector, sender, 20)
        await wait_for(lambda: len(spooled(layout, "switch-2")) == 20)
        rewrite(config_path, good + "\n")
        await wait_for(lambda: status(layout)["config"]["error"] is None)
        assert status(layout)["config"]["sha256"] == sha(config_path)

    with_collector(config_path, body)
    assert not (layout.spool / "unknown-127.0.0.2").exists()


def test_a_listener_change_is_reported_not_applied(
    config_path: Path, sender: socket.socket
) -> None:
    layout = Layout(load_config(config_path).data_dir)

    async def body(collector: Collector) -> None:
        listening = collector.udp_address
        await wait_for(lambda: layout.collector_status.exists())
        text = config_path.read_text(encoding="utf-8")
        rewrite(config_path, text.replace('bind = "127.0.0.1"', 'bind = "127.0.0.3"') + SWITCH)
        await wait_for(lambda: status(layout)["config"]["restart_keys"] == ["collector"])
        assert collector.udp_address == listening
        assert collector.config.collector.bind == "127.0.0.1"
        await send(collector, sender, 5)  # the sources were taken all the same
        await wait_for(lambda: len(spooled(layout, "switch-2")) == 5)

    with_collector(config_path, body)


def test_a_switched_off_source_is_still_recorded_under_its_name(
    config_path: Path, sender: socket.socket
) -> None:
    """``enabled = false`` closes the firewall for the sender; a datagram that still arrives is
    raw evidence and is kept, under the source's own name."""
    rewrite(config_path, config_path.read_text(encoding="utf-8") + SWITCH)
    layout = Layout(load_config(config_path).data_dir)

    async def body(collector: Collector) -> None:
        await wait_for(lambda: layout.collector_status.exists())
        rewrite(config_path, config_path.read_text(encoding="utf-8") + "enabled = false\n")
        await wait_for(lambda: not collector.config.sources[1].enabled)
        await send(collector, sender, 3)
        await wait_for(lambda: len(spooled(layout, "switch-2")) == 3)

    with_collector(config_path, body)
    assert not (layout.spool / "unknown-127.0.0.2").exists()


def test_without_a_file_there_is_no_reload_and_no_config_block(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path))
    layout = Layout(config.data_dir)

    async def scenario() -> None:
        collector = Collector(config)
        await collector.start()
        try:
            await wait_for(lambda: layout.collector_status.exists())
            assert not collector.reload_config()
        finally:
            await collector.stop()

    asyncio.run(scenario())
    assert "config" not in status(layout)


def test_run_reloads_from_its_configuration_file(config_path: Path) -> None:
    """The service entry point (``arcilogger-collector run``) watches the file it started with,
    within :data:`~arcilogger_collector.service.RELOAD_SECONDS`."""
    layout = Layout(load_config(config_path).data_dir)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(run(load_config(config_path), stop, config_path=config_path))
        await wait_for(lambda: layout.collector_status.exists())
        assert status(layout)["config"]["sha256"] == sha(config_path)
        rewrite(config_path, config_path.read_text(encoding="utf-8") + SWITCH)
        await wait_for(lambda: status(layout)["config"]["sha256"] == sha(config_path))
        stop.set()
        await task

    asyncio.run(scenario())


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "arcilogger.toml"
    path.write_text(TEXT.format(data=(tmp_path / "data").as_posix()), encoding="utf-8")
    return path
