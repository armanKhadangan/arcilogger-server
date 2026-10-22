"""Test helpers: a data directory with days sealed by the mock TSA (never legally valid).

Used by the worker's evidence-job tests and by the API's evidence and sealing tests (the API
depends on this package). Everything runs the real ``arcilogger-core`` code — sealing, retention,
replica — with a movable clock, so the vault, the manifests, the job state files and the deletion
records are exactly what the scheduled tasks write on the server.

Sealed files are made read-only (on Linux as root even immutable, ``chattr +i``); call
:meth:`SealedVault.unlock_all` before a test's temporary folder is removed, and
:meth:`SealedVault.tamper` to change a sealed file on purpose.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from arcilogger_core.config import Config, RetentionConfig
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.replica import ReplicaOutcome, replicate_day
from arcilogger_core.retention import RetentionResult, run_retention
from arcilogger_core.seal import Sealer, SealOutcome
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockFailure, MockTsa, MockTsaProvider

SOURCES: tuple[dict[str, Any], ...] = (
    {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
    {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
)
SENDERS = {source["name"]: source["address"] for source in SOURCES}


def at_site(day: date, hour: int = 0, minute: int = 30) -> datetime:
    """``day hour:minute`` in Europe/Istanbul, as UTC."""
    return datetime.combine(day, time(hour, minute), tzinfo=SITE_TZ).astimezone(UTC)


def fortigate_line(day: date, hour: int, minute: int) -> bytes:
    return (
        f'<189>date={day.isoformat()} time={hour:02d}:{minute:02d}:00 devname="FIREWALL-01" '
        f'logid="0000000013" type="traffic" subtype="forward" srcip=172.20.42.{100 + hour} '
        f'srcport={40000 + minute} dstip=203.0.113.10 dstport=443 proto=6 action="close" '
        f"transip=100.64.12.9 transport={50000 + minute}"
    ).encode()


def mikrotik_line(hour: int) -> bytes:
    return (
        f"<30>dhcp,info dhcp60 assigned 172.20.60.{100 + hour} for 02:00:5E:00:53:{hour:02X} "
        "Telefon"
    ).encode()


@dataclass
class TsaClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass
class SealedVault:
    """A data directory, its mock TSA and the clocks the sealing tasks see."""

    root: Path
    config: Config
    tsa: MockTsa
    tsa_clock: TsaClock
    #: What was written: (day, source) → raw messages in line order.
    sent: dict[tuple[date, str], list[bytes]] = field(default_factory=dict)

    @property
    def layout(self) -> Layout:
        return Layout(self.config.data_dir)

    def write_day(self, day: date, sources: Sequence[str] = ("fortigate", "core-r1")) -> None:
        """FortiGate every 30 minutes, the MikroTik every hour (24 + 48 raw lines)."""
        plan: dict[str, list[tuple[time, bytes]]] = {
            "fortigate": [
                (time(h, m), fortigate_line(day, h, m)) for h in range(24) for m in (0, 30)
            ],
            "core-r1": [(time(h, 5), mikrotik_line(h)) for h in range(24)],
        }
        for source in sources:
            path = self.layout.spool_file(source, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                for at, message in plan[source]:
                    received = datetime.combine(day, at, tzinfo=SITE_TZ)
                    handle.write(format_line(received, SENDERS[source], message))
            self.sent.setdefault((day, source), []).extend(message for _, message in plan[source])

    def sealer(self, now: datetime) -> Sealer:
        self.tsa_clock.now = now + timedelta(seconds=5)
        return Sealer(
            self.config,
            provider=MockTsaProvider(self.tsa),
            roots=[self.tsa.root_certificate],
            clock=lambda: now,
            clock_status=lambda: "test clock status",
        )

    def seal(self, day: date, now: datetime | None = None) -> SealOutcome:
        """Seal one day (by default 30 minutes after it ended)."""
        return self.sealer(now or at_site(day + timedelta(days=1))).seal_day(day)

    def seal_days(self, days: Sequence[date]) -> list[SealOutcome]:
        """Write and seal consecutive days, each right after it ended."""
        outcomes = []
        for day in days:
            self.write_day(day)
            outcomes.append(self.seal(day))
        if any(outcome.status != "sealed" for outcome in outcomes):
            raise AssertionError(f"sealing failed: {outcomes}")
        return outcomes

    def fail_seal(self, day: date, failure: MockFailure = MockFailure.UNREACHABLE) -> SealOutcome:
        """A sealing attempt that fails (the day stays pending with the error)."""
        self.tsa.failure = failure
        try:
            return self.seal(day)
        finally:
            self.tsa.failure = MockFailure.NONE

    def retention(self, now: datetime, *, days: int = 730) -> RetentionResult:
        """Run retention as the scheduled task would at ``now``."""
        config = self.config.model_copy(
            update={"retention": RetentionConfig(enabled=True, days=days)}
        )
        self.tsa_clock.now = now + timedelta(seconds=5)
        return run_retention(
            config,
            provider=MockTsaProvider(self.tsa),
            roots=[self.tsa.root_certificate],
            clock=lambda: now,
        )

    def replicate(self, day: date, target: Path) -> ReplicaOutcome:
        return replicate_day(self.layout, day, target)

    def write_config(self, path: Path) -> Path:
        """An ``arcilogger.toml`` for running ``python -m arcilogger_core`` on this vault."""
        sources = "".join(
            f'\n[[sources]]\nname = "{s["name"]}"\ntype = "{s["type"]}"\n'
            f'address = "{s["address"]}"\n'
            for s in SOURCES
        )
        path.write_text(
            f'data_dir = "{self.config.data_dir.as_posix()}"\n'
            f'secrets_dir = "{self.root.as_posix()}"\n'
            '[seal]\nquiet_seconds = 0\n[tsa]\nprovider = "mock"\n' + sources,
            encoding="utf-8",
        )
        return path

    def core(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run a scheduled-task command (``python -m arcilogger_core ...``) on this vault."""
        config = self.write_config(self.root / "arcilogger-test.toml")
        return subprocess.run(  # noqa: S603 - fixed interpreter and module
            [sys.executable, "-m", "arcilogger_core", "--config", str(config), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=300,
        )

    def tamper(self, path: Path, data: bytes = b"tampered\n") -> None:
        """Change a sealed file (after unlocking it, as an administrator could)."""
        if is_locked(path):
            unlock(path)
        with path.open("ab") as handle:
            handle.write(data)

    def unlock_all(self) -> None:
        for path in self.root.rglob("*"):
            if path.is_file() and is_locked(path):
                unlock(path)


def make_vault(data_dir: Path, *, root: Path | None = None, tsa_now: datetime) -> SealedVault:
    """A vault in ``data_dir`` (the API's data directory in API tests) with an EC mock TSA."""
    base = root or data_dir.parent
    config = Config.model_validate(
        {
            "data_dir": str(data_dir),
            "secrets_dir": str(base),
            "sources": list(SOURCES),
            "seal": {"quiet_seconds": 0, "gzip_level": 6},
            "tsa": {"provider": "mock"},
        }
    )
    clock = TsaClock(tsa_now)
    tsa = MockTsa(Layout(data_dir).state / "mock-tsa", key_type="ec", clock=clock)
    return SealedVault(root=base, config=config, tsa=tsa, tsa_clock=clock)
