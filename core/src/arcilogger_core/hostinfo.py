"""Processor, memory and uptime of the server for the Sistem page (Wave 4 contract 2.5.2).

Standard library only, no third-party package (decision D10, owner question Q16): on Windows
``kernel32`` through :mod:`ctypes` (``GetSystemTimes``, ``GlobalMemoryStatusEx``,
``GetTickCount64``), on Linux the files ``/proc/stat``, ``/proc/meminfo`` and ``/proc/uptime``.
On any other platform, or when a reading fails, the answer is ``None`` (the page shows "bu
platformda okunamıyor").

Processor use is a difference of two readings: :func:`cpu_times` is a counter since the machine
started, :func:`cpu_percent` the share of busy time between two of them. The caller keeps the
previous reading (the API keeps one per process); a single reading says nothing about the load.
The figures are the whole machine's (all processors), never a process's.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True, slots=True)
class CpuTimes:
    """Processor time since the machine started, all processors together, in the platform's
    unit (Linux: clock ticks; Windows: 100 ns). Only differences of two readings mean
    anything."""

    busy: int
    idle: int


@dataclass(frozen=True, slots=True)
class Memory:
    total_bytes: int
    #: Memory a new program could use without swapping (Linux ``MemAvailable``, Windows
    #: ``ullAvailPhys``).
    available_bytes: int

    @property
    def used_bytes(self) -> int:
        return max(self.total_bytes - self.available_bytes, 0)


#: Where the Linux readers find the kernel's files (a test points it elsewhere).
PROC: Final = Path("/proc")


def cpu_percent(before: CpuTimes, after: CpuTimes) -> int | None:
    """Busy share of the processor time between two readings, rounded to a whole percent;
    ``None`` when no time passed or the counters went back (another boot)."""
    busy = after.busy - before.busy
    idle = after.idle - before.idle
    if busy < 0 or idle < 0 or busy + idle <= 0:
        return None
    return round(100 * busy / (busy + idle))


def parse_proc_stat(text: str) -> CpuTimes | None:
    """The ``cpu`` line of ``/proc/stat``: ``user nice system idle iowait irq softirq steal
    guest guest_nice`` (clock ticks). Waiting for input/output counts as idle; ``guest`` and
    ``guest_nice`` are already part of ``user`` and ``nice`` and are not added again."""
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] != "cpu":
            continue
        values = [int(value) for value in fields[1:9]]
        if len(values) < 4:
            return None
        values += [0] * (8 - len(values))
        user, nice, system, idle, iowait, irq, softirq, steal = values
        return CpuTimes(busy=user + nice + system + irq + softirq + steal, idle=idle + iowait)
    return None


def parse_meminfo(text: str) -> Memory | None:
    """``MemTotal`` and ``MemAvailable`` of ``/proc/meminfo`` (given in kB = 1024 bytes)."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name in ("MemTotal", "MemAvailable"):
            values[name] = int(rest.split()[0]) * 1024
    if len(values) != 2:
        return None
    return Memory(total_bytes=values["MemTotal"], available_bytes=values["MemAvailable"])


def parse_uptime(text: str) -> int | None:
    """Whole seconds since the machine started: the first number of ``/proc/uptime``."""
    fields = text.split()
    return int(float(fields[0])) if fields else None


def _read_proc[T](name: str, parse: Callable[[str], T | None]) -> T | None:
    try:
        return parse((PROC / name).read_text(encoding="ascii"))
    except (OSError, ValueError, IndexError):
        return None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = (
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        )

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _system_times = _kernel32.GetSystemTimes
    _system_times.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
    _system_times.restype = wintypes.BOOL
    _memory_status = _kernel32.GlobalMemoryStatusEx
    _memory_status.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
    _memory_status.restype = wintypes.BOOL
    _tick_count = _kernel32.GetTickCount64
    _tick_count.argtypes = []
    _tick_count.restype = ctypes.c_uint64

    def _filetime(value: wintypes.FILETIME) -> int:
        return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

    def _windows_cpu_times() -> CpuTimes | None:
        # The kernel time includes the idle time.
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not _system_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        idle_time = _filetime(idle)
        return CpuTimes(busy=_filetime(kernel) + _filetime(user) - idle_time, idle=idle_time)

    def _windows_memory() -> Memory | None:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not _memory_status(ctypes.byref(status)):
            return None
        return Memory(
            total_bytes=int(status.ullTotalPhys), available_bytes=int(status.ullAvailPhys)
        )

    def _windows_uptime() -> int:
        return int(_tick_count()) // 1000


def _on_linux() -> bool:
    return sys.platform.startswith("linux")


def _linux_cpu_times() -> CpuTimes | None:
    return _read_proc("stat", parse_proc_stat) if _on_linux() else None


def _linux_memory() -> Memory | None:
    return _read_proc("meminfo", parse_meminfo) if _on_linux() else None


def _linux_uptime() -> int | None:
    return _read_proc("uptime", parse_uptime) if _on_linux() else None


def cpu_times() -> CpuTimes | None:
    """This machine's processor time counters, or ``None`` (other platform, reading failed)."""
    if sys.platform == "win32":
        reader = _windows_cpu_times
    else:
        reader = _linux_cpu_times
    return reader()


def memory() -> Memory | None:
    """Installed and available memory, or ``None`` (other platform, reading failed)."""
    if sys.platform == "win32":
        reader = _windows_memory
    else:
        reader = _linux_memory
    return reader()


def uptime_seconds() -> int | None:
    """Seconds since the machine started, or ``None`` (other platform, reading failed)."""
    if sys.platform == "win32":
        reader = _windows_uptime
    else:
        reader = _linux_uptime
    return reader()
