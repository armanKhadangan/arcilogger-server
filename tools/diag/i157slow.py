"""Test-only slowdown of the event loop for api/tests/test_live_perf.py (#157 demonstration).

Loaded with ``-p i157slow`` (PYTHONPATH=this folder). I157_SLOW=<factor>, e.g. 1.2: at each hub
cycle's start the event loop thread burns (factor - 1) times the CPU it spent outside the hub's
cycles since the previous cycle's start (the SSE side the test measures), before the test's own
cycle timer reads the clock, so the burn counts as SSE-side work. I157_FRAME_US=<us> instead
burns a fixed CPU time in every ``Entry.frame`` (each delivered line's frame).
Nothing in the repository is changed; the monkeypatches end with the test.
"""

from __future__ import annotations

import os

import pytest

FACTOR = float(os.environ.get("I157_SLOW", "1"))
FRAME_US = float(os.environ.get("I157_FRAME_US", "0"))


def _burn(cpu, seconds):  # type: ignore[no-untyped-def]
    end = cpu() + seconds
    x = 0
    while cpu() < end:
        x += 1
    return x


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):  # type: ignore[no-untyped-def]
    module = item.module
    if not hasattr(module, "LoopCpu") or item.name != "test_three_streams_under_ten_thousand_lines_a_second":
        yield
        return
    init = module.LoopCpu.__init__
    cpu = module._thread_cpu  # seconds on Linux; clock cycles on Windows (not used there here)
    burnt = [0.0]
    restore = []

    def slowed_init(self, hub, monkeypatch):  # type: ignore[no-untyped-def]
        init(self, hub, monkeypatch)
        if FACTOR > 1:
            timed = hub._cycle

            async def slowed_cycle():  # type: ignore[no-untyped-def]
                if self.marks:
                    outside = cpu() - self.cycles - self.marks[-1][0]
                    began = cpu()
                    _burn(cpu, outside * (FACTOR - 1))
                    burnt[0] += cpu() - began
                await timed()

            monkeypatch.setattr(hub, "_cycle", slowed_cycle)
        if FRAME_US > 0:
            from arcilogger_api.live import events

            frame = events.Entry.frame

            def slowed_frame(entry, epoch, site):  # type: ignore[no-untyped-def]
                began = cpu()
                _burn(cpu, FRAME_US * 1e-6)
                burnt[0] += cpu() - began
                return frame(entry, epoch, site)

            monkeypatch.setattr(events.Entry, "frame", slowed_frame)

    module.LoopCpu.__init__ = slowed_init
    try:
        yield
    finally:
        module.LoopCpu.__init__ = init
        tr = item.config.pluginmanager.get_plugin("terminalreporter")
        (tr.write_line if tr else print)(
            f"\n[i157slow] factor {FACTOR}, frame burn {FRAME_US} us: {burnt[0]:.3f} s burnt on the loop"
        )
