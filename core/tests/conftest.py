"""Shared fixtures: a complete ARCILOGGER data directory with a mock TSA and a movable clock."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from time import monotonic
from typing import Any, Self

import pytest

from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ, day_window, site_day, utc_now
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider

FORMAT = Path(__file__).parents[2] / "tests" / "fixtures" / "format"
FORTIGATE_LINES = (FORMAT / "fortigate-sample.log").read_bytes().splitlines()

SOURCES: list[dict[str, Any]] = [
    {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1", "heartbeat_minutes": 10},
    {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
    {"name": "switch-1", "type": "mikrotik", "address": "172.20.10.21"},
]
SENDERS = {source["name"]: source["address"] for source in SOURCES}


@dataclass
class World:
    root: Path
    config: Config
    tsa: MockTsa
    now: datetime
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def layout(self) -> Layout:
        return Layout(self.config.data_dir)

    def clock(self) -> datetime:
        return self.now

    def reconfigure(self, **overrides: Any) -> None:
        self.config = make_config(self.root, **overrides)

    def set_now(self, day: date, hh: int = 0, mm: int = 30) -> None:
        self.now = datetime.combine(day, time(hh, mm), tzinfo=SITE_TZ).astimezone(UTC)

    def sealer(self, **kwargs: Any) -> Sealer:
        return Sealer(
            self.config,
            provider=MockTsaProvider(self.tsa),
            roots=[self.tsa.root_certificate],
            clock=self.clock,
            clock_status=lambda: "test clock status",
            **kwargs,
        )

    def write_spool(self, source: str, day: date, entries: Iterable[tuple[time, bytes]]) -> Path:
        path = self.layout.spool_file(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for at, message in entries:
                received = datetime.combine(day, at, tzinfo=SITE_TZ)
                handle.write(format_line(received, SENDERS.get(source, "10.9.9.9"), message))
        return path

    def busy_day(self, day: date) -> None:
        """FortiGate every 10 min all day, CORE-R1 with a long silence, no switch-1."""
        fortigate = [
            (time(h, m), FORTIGATE_LINES[(h * 6 + m // 10) % len(FORTIGATE_LINES)])
            for h in range(24)
            for m in range(0, 60, 10)
        ]
        mikrotik = [
            (
                time(h, 5),
                f"<30>dhcp,info dhcp60 assigned 172.20.60.{100 + h} for "
                f"02:00:5E:00:53:{h:02X} iPhone".encode(),
            )
            for h in (*range(0, 9), *range(14, 24))  # silent 09:05-14:05
        ]
        self.write_spool("fortigate", day, fortigate)
        self.write_spool("core-r1", day, mikrotik)


def make_config(root: Path, **overrides: Any) -> Config:
    data: dict[str, Any] = {
        "data_dir": str(root / "data"),
        "secrets_dir": str(root),
        "sources": SOURCES,
        "seal": {"quiet_seconds": 0, "gzip_level": 6},
        "tsa": {"provider": "mock"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return Config.model_validate(data)


@pytest.fixture(autouse=True)
def _unlock_after_test(tmp_path: Path) -> Iterator[None]:
    """Sealed files are made immutable; unlock them so pytest can clean its temp folders."""
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def one_site_day(monkeypatch: pytest.MonkeyPatch) -> date:
    """Keep every default clock of the test on one site day, and return that day.

    The CLI, the sealer, the mock TSA and ``status`` read :func:`utc_now`. A test that takes
    its "today" from the real clock and then runs the CLI made that day a closed day without
    data when a site midnight (21:00 UTC) passed in between: ``seal`` sealed it as one more,
    empty day, or ``status`` found yesterday unsealed. This clock starts at the real moment,
    moved back to an hour before the next site midnight at the latest (a test takes seconds,
    also on Windows), and runs at the real rate. It is never ahead of the system clock, which
    the TSA verifier checks a token's genTime against.
    """
    real = utc_now()
    today = site_day(real)
    start = min(real, day_window(today)[1] - timedelta(hours=1)).timestamp()
    started = monotonic()

    class SiteDayClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(start + (monotonic() - started), tz)

    monkeypatch.setattr("arcilogger_core.timeutil.datetime", SiteDayClock)
    return today


@pytest.fixture
def world(tmp_path: Path) -> World:
    config = make_config(tmp_path)
    tsa = MockTsa(Layout(config.data_dir).state / "mock-tsa", key_type="ec")
    return World(
        root=tmp_path,
        config=config,
        tsa=tsa,
        now=datetime(2026, 10, 13, 0, 30, tzinfo=SITE_TZ).astimezone(UTC),
    )


DAYS = [date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)]


def next_day(day: date) -> date:
    return day + timedelta(days=1)


@pytest.fixture
def days() -> list[date]:
    """Three consecutive receive days, the last one closed at the fixture's 'now'."""
    return list(DAYS)
