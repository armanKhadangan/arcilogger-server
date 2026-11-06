"""The internet line's speed samples (``web/e2e/samples/activity-wan.json``; Wave 5 §6 N6, W5-N).

``capture_api_samples.py`` calls :func:`capture` on its main site with the capture and three
signed-in clients (an administrator, an auditor and an administrator who has not accepted the
KVKK notice). It returns ``{sample file name: data}``.

``GET /activity/wan`` answers from ``state/wan-rates.json`` only, which the worker's
``snmp.poll`` job writes on a site. Here the file is written as that job leaves it at the
capture's "now" (13.10.2026 07:00), with the worker's own classes: the FortiGate ``fw-1`` with
two WAN lines, ``wan1`` read every 15 seconds for the last hour (about 40 Mbit/s down and 6 up,
with a 1-minute average per minute for longer) and ``wan2`` that has not answered for
12 minutes (``timeout``), and the MikroTik ``core-r1`` whose SNMP was switched off
(``disabled``).

What ``activity-wan.json`` holds: ``hour`` (``range=60m``, the administrator's answer),
``day`` (``range=24h``, the auditor's: ``dashboard.view``), ``empty`` (no file: no source has
SNMP on) and ``errors``: ``not_authenticated`` (no session). The file is put back afterwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient
from samples_system import kept

from arcilogger_core.timeutil import utc_now
from arcilogger_worker.snmp.state import Counters, Line, SourceState, WanState, write_state

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: The keys compared as values besides the defaults: the line's interface state and the range.
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "activity-wan": (frozenset(), frozenset({"oper_status", "range"})),
}

API = "/api/v1/activity/wan"
#: How long ``wan1`` has been read, and how often.
READ_FOR = timedelta(hours=2)
EVERY = timedelta(seconds=15)


def wan_state(now: datetime) -> WanState:
    """``state/wan-rates.json`` as ``snmp.poll`` leaves it at ``now`` (see the module text)."""
    wan1 = Line(
        name="wan1",
        if_index=3,
        oper_status="up",
        high_speed_mbps=1000,
        capacity=(100.0, 20.0),
        updated_at=now,
        counters=Counters(812_455_901_337, 98_310_442_118, now),
    )
    at, step = now - READ_FOR, 0
    while at <= now:
        # a slow wave between 34 and 46 Mbit/s down, 5 and 7 up: whole bits per second
        wave = (step % 40 - 20) / 20
        wan1.add(at, 40_000_000 + 6_000_000 * wave, 6_000_000 + 1_000_000 * wave)
        at, step = at + EVERY, step + 1
    wan2 = Line(
        name="wan2",
        if_index=4,
        oper_status="up",
        high_speed_mbps=100,
        status="timeout",
        last_error_at=now,
        updated_at=now - timedelta(minutes=12),
        counters=Counters(55_102_771, 9_801_266, now - timedelta(minutes=12)),
    )
    isp = Line(name="ether1", if_index=1, status="disabled")
    return WanState(
        {
            "fw-1": SourceState(
                "fw-1",
                uptime=815_220_100,
                polled_at=now,
                watched_since=now - READ_FOR,
                lines={"wan1": wan1, "wan2": wan2},
            ),
            "core-r1": SourceState("core-r1", lines={"ether1": isp}),
        }
    )


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The internet line's speed samples (see the module text)."""
    path = capture.layout.wan_rates
    out: dict[str, Any] = {}
    with kept(path):
        path.unlink(missing_ok=True)
        out["empty"] = capture.json(admin.get(API), 200)
        now = utc_now()
        write_state(path, wan_state(now), now)
        out["hour"] = capture.json(admin.get(API, params={"range": "60m"}), 200)
        out["day"] = capture.json(auditor.get(API, params={"range": "24h"}), 200)
    if out["empty"]["lines"] != [] or len(out["hour"]["lines"]) != 3:
        raise SystemExit(f"activity-wan: unexpected answers {out['empty']}, {out['hour']}")
    anonymous = TestClient(capture.app, base_url="http://testserver")
    out["errors"] = {
        "not_authenticated": capture.error(anonymous.get(API), 401, "not_authenticated")
    }
    return {"activity-wan": out}
