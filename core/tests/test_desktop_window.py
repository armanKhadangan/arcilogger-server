"""The desktop status window's Tk view (``arcilogger_core.desktop.window``).

These tests build the real window, so they need a display: they run on Windows and where
``DISPLAY`` is set (locally ``xvfb-run -a uv run pytest core/tests/test_desktop_window.py``) and
are skipped otherwise (``ARCILOGGER_REQUIRE_DISPLAY=1`` turns the skip into a failure), like the
Lisans Yöneticisi's window tests.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

pytest.importorskip("tkinter", reason="this Python has no tkinter")

import tkinter as tk

from arcilogger_core.desktop import window
from arcilogger_core.desktop.status import Row, Snapshot, window_title


def _display() -> bool:
    return sys.platform == "win32" or bool(os.environ.get("DISPLAY"))


pytestmark = pytest.mark.skipif(
    not _display() and os.environ.get("ARCILOGGER_REQUIRE_DISPLAY") != "1",
    reason="no display (run under xvfb-run)",
)


@pytest.fixture
def root() -> Iterator[tk.Tk]:
    root = tk.Tk()
    root.withdraw()
    yield root
    with contextlib.suppress(tk.TclError):  # the test closed it
        root.destroy()


def labels(box: tk.Misc) -> list[str]:
    return [
        str(child.cget("text"))
        for child in box.winfo_children()
        if not isinstance(child, tk.Canvas)
    ]


def test_self_test() -> None:
    assert window.self_test() == 0


def test_rows_are_shown_in_both_languages(root: tk.Tk) -> None:
    shown = window.StatusWindow(root, refresh=lambda: window.sample("warn"), background=False)
    assert root.title() == window_title("warn")
    assert labels(shown.check_box) == [
        "Log akışı",
        "2 kaynak, 1 sessiz: fw-ornek",
        "Log flow",
        "2 sources, 1 silent: fw-ornek",
    ]
    assert "Son bakış / Last look:" in str(shown.updated.cget("text"))


def test_rows_are_rebuilt_when_their_keys_change(root: tk.Tk) -> None:
    snapshots = iter(
        [
            window.sample("ok"),
            Snapshot(
                at=window.sample("ok").at,
                services=(),
                checks=(
                    Row("flow", "ok", "Log akışı", "Log flow", "a", "b"),
                    Row("disk", "warn", "Veri diski", "Data drive", "c", "d"),
                ),
            ),
        ]
    )
    shown = window.StatusWindow(root, refresh=lambda: next(snapshots), background=False)
    assert len(shown.service_box.winfo_children()) == 10
    shown.refresh_now()
    assert labels(shown.check_box) == [
        "Log akışı",
        "a",
        "Log flow",
        "b",
        "Veri diski",
        "c",
        "Data drive",
        "d",
    ]
    assert shown.service_box.winfo_children() == []


def test_a_failing_refresh_keeps_the_window_up(root: tk.Tk) -> None:
    def broken() -> Snapshot:
        raise RuntimeError("boom")

    shown = window.StatusWindow(root, refresh=broken, background=False)
    assert shown.level == "warn"
    assert "RuntimeError: boom" in labels(shown.check_box)[1]


def wait_for(root: tk.Tk, condition: Callable[[], bool], seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        root.update()
        time.sleep(0.02)


def test_the_background_thread_feeds_the_window(root: tk.Tk) -> None:
    taken: list[int] = []
    flashed: list[int] = []

    def refresh() -> Snapshot:
        taken.append(1)
        return window.sample("critical")

    shown = window.StatusWindow(root, refresh=refresh, interval_ms=60_000, flash=flashed.append)
    wait_for(root, lambda: shown.level == "critical")
    assert len(flashed) == 1
    shown.refresh_now()  # wakes the thread at once instead of after a minute
    wait_for(root, lambda: len(taken) >= 2)
    shown.close()
    assert shown._stop.is_set()


def test_open_web_uses_localhost(root: tk.Tk) -> None:
    opened: list[Any] = []
    shown = window.StatusWindow(
        root, refresh=lambda: window.sample("ok"), open_url=opened.append, background=False
    )
    shown.open_web()
    assert opened == ["https://localhost/"]
