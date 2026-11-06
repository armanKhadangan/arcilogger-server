"""``GET /api/v1/activity/wan`` (Wave 5 contract §6 N6, W5-N).

The answer comes from ``state/wan-rates.json`` only (the worker's ``snmp.poll`` writes it): the
newest rates, the series of each range, the status and times of each line, for
``dashboard.view`` or ``system.read``, never a key.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from arcilogger_api.activity import wan
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now
from arcilogger_worker.snmp.state import Counters, Line, SourceState, WanState, write_state

Login = Callable[..., LoggedIn]
URL = "/api/v1/activity/wan"


def _write(layout: Layout) -> None:
    now = utc_now()
    up = Line(
        name="wan1",
        if_index=1,
        oper_status="up",
        high_speed_mbps=1000,
        capacity=(1000.0, 100.0),
        updated_at=now - timedelta(seconds=10),
        counters=Counters(10, 20, now - timedelta(seconds=10)),
    )
    for minutes in (120, 90, 30, 0):  # a rate every 30 minutes; the first two are older than 60m
        at = now - timedelta(minutes=minutes, seconds=10)
        up.add(at, 8_000_000.0 + minutes, 800_000.0)
    down = Line(name="wan2", status="timeout", last_error_at=now - timedelta(seconds=5))
    other = Line(name="isp", status="disabled")
    write_state(
        layout.wan_rates,
        WanState(
            {
                "fw-1": SourceState("fw-1", lines={"wan1": up, "wan2": down}),
                "core-r1": SourceState("core-r1", lines={"isp": other}),
            }
        ),
        now,
    )


def test_the_lines_of_the_last_hour(client: TestClient, login_as: Login, layout: Layout) -> None:
    _write(layout)
    login_as(client, "viewer")  # dashboard.view
    answer = client.get(URL)
    assert answer.status_code == 200, answer.text
    data = answer.json()
    assert data["range"] == "60m"
    assert [(line["source"], line["name"]) for line in data["lines"]] == [
        ("core-r1", "isp"),
        ("fw-1", "wan1"),
        ("fw-1", "wan2"),
    ]
    isp, wan1, wan2 = data["lines"]
    assert (wan1["status"], wan1["oper_status"]) == ("ok", "up")
    assert (wan1["download_bps"], wan1["upload_bps"]) == (8_000_000.0, 800_000.0)
    assert wan1["capacity_mbps"] == {"down": 1000.0, "up": 100.0}
    assert [point["download_bps"] for point in wan1["series"]] == [8_000_030.0, 8_000_000.0]
    assert wan1["updated_at"] is not None
    assert (wan2["status"], wan2["download_bps"], wan2["series"]) == ("timeout", None, [])
    assert wan2["last_error_at"] is not None
    assert (isp["status"], isp["oper_status"], isp["capacity_mbps"]) == ("disabled", None, None)


def test_the_last_day_as_minute_averages(
    client: TestClient, login_as: Login, layout: Layout
) -> None:
    _write(layout)
    login_as(client, "auditor")  # dashboard.view as well
    data = client.get(URL, params={"range": "24h"}).json()
    wan1 = next(line for line in data["lines"] if line["name"] == "wan1")
    assert [point["download_bps"] for point in wan1["series"]] == [
        8_000_120.0,
        8_000_090.0,
        8_000_030.0,
        8_000_000.0,
    ]


def test_no_file_no_lines(client: TestClient, login_as: Login) -> None:
    login_as(client, "admin")
    data = client.get(URL).json()
    assert (data["range"], data["lines"]) == ("60m", [])


@pytest.mark.parametrize("value", ["1h", "60", ""])
def test_an_unknown_range_is_refused(client: TestClient, login_as: Login, value: str) -> None:
    login_as(client, "admin")
    assert client.get(URL, params={"range": value}).status_code == 422


def test_who_may_read_it(client: TestClient, login_as: Login, layout: Layout) -> None:
    _write(layout)
    assert client.get(URL).status_code == 401
    login_as(client, "hr")  # neither dashboard.view nor system.read
    answer = client.get(URL)
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "forbidden"


def test_the_answer_is_computed_from_the_file_only(layout: Layout) -> None:
    """No request to a device and no database: an empty data folder gives an empty answer."""
    answer = wan.wan_rates(layout, "24h", utc_now())
    assert answer.lines == []
