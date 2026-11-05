"""Processor, memory and uptime readers of the Sistem page (Wave 4 contract 2.5.2, 3 W4-B)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from arcilogger_core import hostinfo
from arcilogger_core.hostinfo import (
    CpuTimes,
    Memory,
    cpu_percent,
    parse_meminfo,
    parse_proc_stat,
    parse_uptime,
)

PROC_STAT = """\
cpu  4705 150 1120 16250 520 12 30 7 300 0
cpu0 2352 75 560 8125 260 6 15 3 150 0
intr 114930548 113199788 3 0 5 263 0 4 [... 50 more items]
ctxt 1990473
btime 1062191376
"""

MEMINFO = """\
MemTotal:       16314356 kB
MemFree:         1213820 kB
MemAvailable:    9471284 kB
Buffers:          612344 kB
Cached:          7340280 kB
"""


def test_linux_readers_parse_proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # user + nice + system + irq + softirq + steal; idle + iowait; guest is part of user.
    assert parse_proc_stat(PROC_STAT) == CpuTimes(busy=4705 + 150 + 1120 + 12 + 30 + 7, idle=16770)
    assert parse_proc_stat("cpu  10 0 5 100\n") == CpuTimes(busy=15, idle=100)  # old kernels
    assert parse_proc_stat("cpu  10 0\n") is None
    assert parse_proc_stat("intr 1 2 3\n") is None
    memory = parse_meminfo(MEMINFO)
    assert memory == Memory(total_bytes=16314356 * 1024, available_bytes=9471284 * 1024)
    assert memory.used_bytes == (16314356 - 9471284) * 1024
    assert parse_meminfo("MemTotal: 1024 kB\n") is None  # kernels before 3.14
    assert parse_uptime("350735.47 234388.90\n") == 350735
    assert parse_uptime("") is None

    # The readers themselves, on any machine: only the files' place is changed.
    monkeypatch.setattr(sys, "platform", "linux")
    (tmp_path / "stat").write_text(PROC_STAT, encoding="ascii")
    (tmp_path / "meminfo").write_text(MEMINFO, encoding="ascii")
    (tmp_path / "uptime").write_text("350735.47 234388.90\n", encoding="ascii")
    monkeypatch.setattr(hostinfo, "PROC", tmp_path)
    assert hostinfo.cpu_times() == parse_proc_stat(PROC_STAT)
    assert hostinfo.memory() == memory
    assert hostinfo.uptime_seconds() == 350735
    (tmp_path / "stat").write_text("cpu  x y z\n", encoding="ascii")
    (tmp_path / "meminfo").unlink()
    (tmp_path / "uptime").write_text("\n", encoding="ascii")
    assert hostinfo.cpu_times() is None
    assert hostinfo.memory() is None
    assert hostinfo.uptime_seconds() is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_linux_readers_read_this_machine() -> None:
    times = hostinfo.cpu_times()
    memory = hostinfo.memory()
    uptime = hostinfo.uptime_seconds()
    assert times is not None
    assert times.busy >= 0
    assert times.idle >= 0
    assert memory is not None
    assert 0 < memory.available_bytes <= memory.total_bytes
    assert uptime is not None
    assert uptime >= 0


def test_cpu_percent_from_two_readings() -> None:
    before = CpuTimes(busy=1000, idle=3000)
    assert cpu_percent(before, CpuTimes(busy=1250, idle=3750)) == 25
    assert cpu_percent(before, CpuTimes(busy=1001, idle=3002)) == 33  # rounded
    assert cpu_percent(before, CpuTimes(busy=2000, idle=3000)) == 100
    assert cpu_percent(before, CpuTimes(busy=1000, idle=4000)) == 0
    assert cpu_percent(before, before) is None  # no time passed
    assert cpu_percent(before, CpuTimes(busy=10, idle=20)) is None  # counters reset (reboot)


def test_other_platform_answers_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert hostinfo.cpu_times() is None
    assert hostinfo.memory() is None
    assert hostinfo.uptime_seconds() is None


@pytest.mark.skipif(sys.platform != "win32", reason="kernel32 readers (Windows CI job)")
def test_windows_readers() -> None:
    first = hostinfo.cpu_times()
    second = hostinfo.cpu_times()
    memory = hostinfo.memory()
    uptime = hostinfo.uptime_seconds()
    assert first is not None
    assert second is not None
    assert second.busy >= first.busy >= 0
    assert second.idle >= first.idle >= 0
    assert memory is not None
    assert 0 < memory.available_bytes <= memory.total_bytes
    assert uptime is not None
    assert uptime > 0
