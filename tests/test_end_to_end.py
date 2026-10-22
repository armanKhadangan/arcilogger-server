"""Collector -> raw spool -> nightly seal -> verification -> the exact bytes back from the vault.

The log format samples (``tests/fixtures/format/``, fictional values) are sent over UDP to a
running collector on three consecutive receive days, sealed with the mock time-stamp authority
and verified. Every message read back from the sealed, compressed vault files must equal, byte
for byte, what was sent.
"""

from __future__ import annotations

import asyncio
import gzip
import socket
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from arcilogger_collector.service import Collector
from arcilogger_core.chain import verify_range
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import parse_line
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider

FORMAT = Path(__file__).parent / "fixtures" / "format"
FORTIGATE = (FORMAT / "fortigate-sample.log").read_bytes().splitlines()
MIKROTIK = [
    b"<30>2026-09-23T09:38:26+03:00 CORE-R1 " + line.split(b"|", 1)[1].strip()
    for line in (FORMAT / "mikrotik-log-print-sample.txt").read_bytes().splitlines()
]
DAYS = [date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)]


@pytest.fixture(autouse=True)
def _unlock(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


def test_messages_survive_collection_sealing_and_verification(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "sources": [{"name": "local", "type": "generic", "address": "127.0.0.1"}],
            "collector": {
                "bind": "127.0.0.1",
                "udp_port": 0,
                "tcp_port": None,
                "fsync_interval_seconds": 0.02,
            },
            "seal": {"quiet_seconds": 0},
        }
    )
    sent: dict[date, list[bytes]] = {day: [] for day in DAYS}
    # Settable clock: the collector reads it for every message and for maintenance.
    now = [datetime(2026, 10, 10, 0, 0, tzinfo=SITE_TZ).astimezone(UTC)]

    async def collect() -> None:
        collector = Collector(config, clock=lambda: now[0])
        await collector.start()
        address = collector.udp_address
        assert address is not None
        expected = 0
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for day in DAYS:
                for hour in range(24):
                    now[0] = datetime.combine(day, time(hour, 15), tzinfo=SITE_TZ).astimezone(UTC)
                    for message in [*FORTIGATE, *MIKROTIK]:
                        sock.sendto(message, address)
                        sent[day].append(message)
                        expected += 1
                        # UDP has no back-pressure: wait until each datagram is written.
                        while collector.stats.sources.get("local") is None or (
                            collector.stats.sources["local"].messages < expected
                        ):
                            await asyncio.sleep(0)
        await collector.stop()

    asyncio.run(collect())

    tsa = MockTsa(tmp_path / "data" / "state" / "mock-tsa", key_type="ec")
    sealer = Sealer(
        config,
        provider=MockTsaProvider(tsa),
        roots=[tsa.root_certificate],
        clock=lambda: datetime(2026, 10, 13, 0, 30, tzinfo=SITE_TZ).astimezone(UTC),
        clock_status=lambda: None,
    )
    assert [o.status for o in sealer.seal_due()] == ["sealed"] * 3

    layout = Layout(config.data_dir)
    checks = verify_range(layout, DAYS, [tsa.root_certificate], deep=True)
    assert [c.errors for c in checks] == [[], [], []]

    for day in DAYS:
        vault = layout.vault_day(day) / "local.log.gz"
        records = [parse_line(line) for line in gzip.decompress(vault.read_bytes()).splitlines()]
        assert [r.message for r in records] == sent[day]
        assert {r.sender for r in records} == {"127.0.0.1"}
        assert all(r.received.astimezone(SITE_TZ).date() == day for r in records)
        assert layout.spool_files_for(day) == []
    assert DAYS[-1] + timedelta(days=1) == date(2026, 10, 13)
