"""Graceful stop on Ctrl+C, SIGTERM and the Windows console events.

The service wrapper on Windows stops a console program with a console control event (Ctrl+C or
Ctrl+Break), and Windows itself sends ``CTRL_CLOSE_EVENT`` (console closed) and
``CTRL_SHUTDOWN_EVENT`` (system shutdown). For the last two the process is terminated as soon as
the handler *returns*, so the handler sets the stop flag and then **waits** (up to a few
seconds; Windows allows about 5 s) until the service loop has loaded or rolled back its current
batch and written its status file.

``CTRL_LOGOFF_EVENT`` is ignored on purpose: a console program running as a service receives it
whenever *any* user logs off the server, which is no reason to stop.

POSIX (development, CI): ``SIGINT`` and ``SIGTERM`` set the stop flag.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Final

#: How long a close/shutdown handler waits for the loop (Windows kills after ~5 s).
WINDOWS_GRACE_SECONDS: Final = 4.5

CTRL_C_EVENT: Final = 0
CTRL_BREAK_EVENT: Final = 1
CTRL_CLOSE_EVENT: Final = 2
CTRL_LOGOFF_EVENT: Final = 5
CTRL_SHUTDOWN_EVENT: Final = 6


def console_event_action(event: int) -> str:
    """What the Windows handler does with a console event: ``stop``, ``stop_and_wait``, ``ignore``.

    Separate from the handler so the decision is testable on every platform.
    """
    if event in (CTRL_CLOSE_EVENT, CTRL_SHUTDOWN_EVENT):
        return "stop_and_wait"
    if event in (CTRL_C_EVENT, CTRL_BREAK_EVENT):
        return "stop"
    return "ignore"  # CTRL_LOGOFF_EVENT and anything unknown


def handle_console_event(
    event: int, stop: threading.Event, stopped: threading.Event, grace: float
) -> bool:
    """Body of the Windows console handler; returns ``True`` (= handled) for every event."""
    action = console_event_action(event)
    if action != "ignore":
        stop.set()
    if action == "stop_and_wait":
        # Windows timers may wake a few milliseconds early (~15.6 ms ticks): wait until the
        # grace period has really passed, not just until the first wake-up.
        deadline = time.monotonic() + grace
        while not stopped.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            stopped.wait(remaining)
    return True


def install_stop_handlers(
    stop: threading.Event,
    stopped: threading.Event,
    *,
    grace: float = WINDOWS_GRACE_SECONDS,
) -> Callable[[], None]:
    """Route the stop requests of this platform to ``stop``; returns a function that undoes it."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(event: int) -> bool:
            return handle_console_event(event, stop, stopped, grace)

        callback = handler_type(_handler)  # must stay referenced while installed
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.SetConsoleCtrlHandler(callback, True):
            raise ctypes.WinError(ctypes.get_last_error())

        def _uninstall() -> None:
            kernel32.SetConsoleCtrlHandler(callback, False)

    else:
        previous = {
            sig: signal.signal(sig, lambda _signum, _frame: stop.set())
            for sig in (signal.SIGINT, signal.SIGTERM)
        }

        def _uninstall() -> None:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    return _uninstall
