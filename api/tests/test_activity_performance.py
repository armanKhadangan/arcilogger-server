"""``GET /api/v1/activity/devices`` under load (the M4 guard (a) of #201, Wave 5 contract §6.2 M4 as
amended by deviation W5-M-4): the answer from a full snapshot comes in under :data:`BUDGET`.

The snapshot is the worker's file at its fullest: every subject sends and receives in each of
the 60 minutes, with :data:`TOP` destinations and applications a minute (the most the worker
keeps). 200 subjects are the default (the most rows the answer lists); 1,000 the site of the
worker's 2,000 lines/s guard. Run with ``-s`` to see the figures (recorded on #211).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.activity import routes
from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout

pytestmark = pytest.mark.perf

API = "/api/v1/activity/devices"
BUDGET = 1.0
TOP = 5
NOW = datetime(2026, 10, 14, 8, 0, 30, tzinfo=UTC)


def snapshot(subjects: int) -> dict[str, object]:
    end = NOW.replace(second=0) + timedelta(minutes=1)
    keys = [f"ip:172.20.{44 + n // 250}.{1 + n % 250}" for n in range(subjects)]
    minutes = []
    for index in range(60):
        moment = end - timedelta(minutes=60 - index)
        minutes.append(
            {
                "minute": moment.isoformat(),
                "subjects": {
                    key: [
                        1000 * (n + 1),
                        100 * (n + 1),
                        3,
                        [[(n + d) % 97, 100 * (d + 1)] for d in range(TOP)],
                        [[(n + a) % 31, 50 * (a + 1)] for a in range(TOP)],
                    ]
                    for n, key in enumerate(keys)
                },
            }
        )
    return {
        "version": 1,
        "generated_at": NOW.isoformat(),
        "from": (end - timedelta(minutes=60)).isoformat(),
        "until": end.isoformat(),
        "warming_up": False,
        "data_until": NOW.isoformat(),
        "subjects": {key: {"kind": "ip", "vlan": 44, "mac": None, "ip": key[3:]} for key in keys},
        "destinations": [[f"site{n}.example", "domain"] for n in range(97)],
        "apps": [f"App.{n}" for n in range(31)],
        "minutes": minutes,
    }


@pytest.mark.parametrize("subjects", [200, 1000])
def test_an_answer_from_a_full_snapshot_is_quick(
    client: TestClient,
    login_as: Callable[..., LoggedIn],
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    subjects: int,
) -> None:
    layout.activity.parent.mkdir(parents=True, exist_ok=True)
    layout.activity.write_text(json.dumps(snapshot(subjects)), encoding="utf-8")
    monkeypatch.setattr(routes, "utc_now", lambda: NOW)
    who = login_as(client, "admin")
    with app_session_factory() as session:
        accept_privacy(session, who.user_id, privacy_version(session), ip=None)
        session.commit()
    timings = []
    for window in ("1", "5", "15", "15"):
        started = time.perf_counter()
        response = client.get(API, params={"window": window})
        timings.append(round(time.perf_counter() - started, 3))
        assert response.status_code == 200, response.text
        assert len(response.json()["rows"]) == min(subjects, 200)
    size = layout.activity.stat().st_size
    print(f"\nactivity answer, {subjects} subjects, snapshot {size} bytes: {timings} s")
    assert max(timings) < BUDGET
