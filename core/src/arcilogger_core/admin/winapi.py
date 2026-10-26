"""Windows calls of the administration agent, with POSIX fallbacks (ADR-0014; contract 2.7.1).

* :func:`replace_file` puts a new configuration file in place in one step. On Windows it is
  ``ReplaceFileW``, which keeps the replaced file's security descriptor, owner and attributes, so
  the explicit ``ArciAdmin`` entry of ``arcilogger.toml`` and ``secrets.json`` survives every
  replace (``os.replace`` would give the file the rights of the temporary file). Python opens
  files without ``FILE_SHARE_DELETE``, so a service reading the file at that moment makes the
  call fail with a sharing violation: :class:`SharingViolationError`, which the caller retries.
  Elsewhere (tests, Linux CI) it is ``os.replace``.
* :class:`WindowsEventLog` writes the Application log with the source ``ARCILOGGER-Admin`` (the
  installer registers it); :class:`LoggingEventLog` is the fallback that only logs. An event never
  carries a secret, and writing one never fails a change.
* :func:`can_write` is the agent's check that it can still open both files for writing after an
  apply (``acl_drift``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Final, Literal, Protocol

from arcilogger_core.admin.contract import EVENT_SOURCE, Event

log = logging.getLogger("arcilogger.admin.events")

EventLevel = Literal["info", "warning", "error"]


class SharingViolationError(OSError):
    """Another process has the file open without sharing it for deletion; try again."""


class EventLog(Protocol):
    def write(self, event: Event, level: EventLevel, message: str) -> None:
        """Record one event (never raises)."""
        ...


class LoggingEventLog:
    """The event log of a machine without one (or without the registered source): the agent's
    log file."""

    _LEVELS: Final = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

    def write(self, event: Event, level: EventLevel, message: str) -> None:
        log.log(self._LEVELS[level], "event %d: %s", int(event), message)


def can_write(path: Path) -> bool:
    """Whether this process can open ``path`` for writing (nothing is written)."""
    try:
        with path.open("r+b"):
            return True
    except OSError:
        return False


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _REPLACEFILE_IGNORE_MERGE_ERRORS: Final = 0x00000002
    _SHARING_ERRORS: Final = frozenset({32, 33})  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
    _EVENT_TYPES: Final = {"error": 0x0001, "warning": 0x0002, "info": 0x0004}

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _replace = _kernel32.ReplaceFileW
    _replace.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    _replace.restype = wintypes.BOOL
    _register = _advapi32.RegisterEventSourceW
    _register.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _register.restype = wintypes.HANDLE
    _report = _advapi32.ReportEventW
    _report.argtypes = [
        wintypes.HANDLE,
        wintypes.WORD,
        wintypes.WORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.WORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.LPVOID,
    ]
    _report.restype = wintypes.BOOL

    def replace_file(replacement: Path, target: Path) -> None:
        """Put ``replacement`` in the place of ``target`` (``ReplaceFileW``; a missing target is
        a plain rename). :class:`SharingViolationError` while another process holds it."""
        if not target.exists():
            replacement.replace(target)
            return
        if _replace(
            str(target), str(replacement), None, _REPLACEFILE_IGNORE_MERGE_ERRORS, None, None
        ):
            return
        code = ctypes.get_last_error()
        text = ctypes.FormatError(code).strip()
        if code in _SHARING_ERRORS:
            raise SharingViolationError(None, text, str(target), code)
        raise OSError(None, text, str(target), code)

    class WindowsEventLog:
        """The Application log, source ``ARCILOGGER-Admin``; falls back to the log file when
        the source cannot be opened."""

        def __init__(self, source: str = EVENT_SOURCE) -> None:
            self._handle = _register(None, source)
            self._fallback = LoggingEventLog()
            if not self._handle:
                log.warning(
                    "event source %s cannot be opened (Windows error %d); events go to the log",
                    source,
                    ctypes.get_last_error(),
                )

        def write(self, event: Event, level: EventLevel, message: str) -> None:
            self._fallback.write(event, level, message)
            if not self._handle:
                return
            strings = (wintypes.LPCWSTR * 1)(message)
            if not _report(
                self._handle, _EVENT_TYPES[level], 0, int(event), None, 1, 0, strings, None
            ):
                log.warning(
                    "event %d not written (Windows error %d)", int(event), ctypes.get_last_error()
                )

    def default_event_log() -> EventLog:
        return WindowsEventLog()

else:

    def replace_file(replacement: Path, target: Path) -> None:
        """Put ``replacement`` in the place of ``target`` (``os.replace``)."""
        replacement.replace(target)

    def default_event_log() -> EventLog:
        return LoggingEventLog()
