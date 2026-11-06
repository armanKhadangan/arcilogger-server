"""``GET /api/v1/activity/devices`` (Wave 5 contract §6.2 M2, M3; tests §6.5 W5-M) on the golden
usage site (:mod:`arcilogger_worker.usage.testing`, 14.10.2026).

"Now" is set on the route (``utc_now``); the golden day's logs are all in the database already,
so only minutes before "now" are asserted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.activity import routes
from arcilogger_api.activity.devices import count_range
from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore
from arcilogger_core.timeutil import day_window
from arcilogger_db.models import AuditLog, Person
from arcilogger_db.settings import set_setting
from arcilogger_worker.testing import tr
from arcilogger_worker.usage.engine import compute_day
from arcilogger_worker.usage.pseudonyms import pseudonym
from arcilogger_worker.usage.testing import DAY, GoldenSite, build_golden_site

API = "/api/v1/activity/devices"
Login = Callable[..., LoggedIn]
PSEUDONYM_KEY = "golden-activity-pseudonym-key-0123456789"  # gitleaks:allow (test-only key)
#: 09:02:30: the first interim log of the 09:00-09:05 session (09:02, 12 000 bytes received,
#: 1 200 sent over its 120 s) has come, its next one (09:04) and its final log (09:05) not yet.
NOW = tr(DAY, 9, 2, 30)


@pytest.fixture
def site(db_session: Session, layout: Layout) -> GoldenSite:
    return build_golden_site(db_session, layout)


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """The route's clock: ``now[0]``, changed by the test."""
    moment = [NOW.astimezone(UTC)]
    monkeypatch.setattr(routes, "utc_now", lambda: moment[0])
    return moment


def _signed_in(
    client: TestClient,
    login_as: Login,
    factory: sessionmaker[Session],
    role: str,
    *,
    notice: bool = True,
) -> LoggedIn:
    who = login_as(client, role)
    if notice:
        with factory() as session:
            accept_privacy(session, who.user_id, privacy_version(session), ip=None)
            session.commit()
    return who


@pytest.fixture
def admin(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: GoldenSite,
    now: list[datetime],
) -> TestClient:
    _signed_in(client, login_as, app_session_factory, "admin")
    return client


def _get(client: TestClient, **params: Any) -> Any:
    response = client.get(API, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _minute(answer: Any, hour: int, minute: int) -> tuple[int, int]:
    start = tr(DAY, hour, minute).astimezone(UTC)
    (found,) = [
        point for point in answer["per_minute"] if datetime.fromisoformat(point["minute"]) == start
    ]
    return found["download_bytes"], found["upload_bytes"]


def test_a_day_of_this_counting_adds_up_to_the_usage_rollup(
    db_session: Session, site: GoldenSite
) -> None:
    """§6.5: the increment rule against the usage engine on the same rows. Over the whole
    golden day the range's bytes equal the day's rollup (each rollup row is rounded)."""
    lo, hi = day_window(DAY)
    builder = count_range(db_session, lo.astimezone(UTC), hi.astimezone(UTC))
    received = sum(
        r
        for cells in builder.cells.values()
        for cell in cells.values()
        for _, r, _ in cell.values()
    )
    sent = sum(
        s
        for cells in builder.cells.values()
        for cell in cells.values()
        for _, _, s in cell.values()
    )
    day = compute_day(db_session, DAY)
    assert abs(received - sum(row["download_bytes"] for row in day.daily)) <= len(day.daily)
    assert abs(sent - sum(row["upload_bytes"] for row in day.daily)) <= len(day.daily)
    assert received > 600_000  # the day has its traffic (Windows Update alone 600 000)


def test_an_interim_delta_counts_at_its_own_minute(admin: TestClient) -> None:
    """§6.5 (X-B1): the first interim log's 12 000 / 1 200 bytes over 09:00-09:02 are in the
    minutes 09:00 and 09:01 before the session's final log (09:05) has come."""
    answer = _get(admin, window="1")
    assert _minute(answer, 9, 0) == (6000, 600)
    assert _minute(answer, 9, 1) == (6000, 600)
    assert _minute(answer, 8, 59) == (0, 0)
    assert len(answer["per_minute"]) == 60
    assert datetime.fromisoformat(answer["data_until"]) == tr(DAY, 9, 2).astimezone(UTC)
    assert datetime.fromisoformat(answer["generated_at"]) == NOW.astimezone(UTC)


def test_the_window_lists_its_subjects(admin: TestClient, site: GoldenSite) -> None:
    """The last minute (09:02-09:03): Ayşe's PC-01 with the video session's next interim
    interval (3 000 / 300 of its 6 000 / 600 over 09:02-09:04) and the mail session named by
    DNS (3 000 / 300): two sessions, two sites, rates over 60 s."""
    answer = _get(admin, window="1")
    assert answer["window"] == 1
    (row,) = answer["rows"]
    assert row["subject"]["label"] == "Ayşe Yılmaz"
    assert row["subject"]["key"] == f"person:{site.ayse}"
    assert (row["download_bytes"], row["upload_bytes"]) == (6000, 600)
    assert row["download_bps"] == pytest.approx(6000 * 8 / 60)
    assert row["sessions"] == 2
    assert {(d["domain"], d["bytes"]) for d in row["top_domains"]} == {
        ("video.example", 3300),
        ("posta.example", 3300),
    }
    assert answer["more"] is False
    assert answer["totals"]["download_bytes"] == 6000
    assert answer["totals"]["subjects"] == 1


def test_an_excluded_person_is_no_row_but_counts_in_the_totals(
    admin: TestClient, app_session_factory: sessionmaker[Session], site: GoldenSite
) -> None:
    with app_session_factory() as session:
        session.execute(update(Person).where(Person.id == site.ayse).values(usage_excluded=True))
        session.commit()
    answer = _get(admin, window="1")
    assert answer["rows"] == []
    assert answer["totals"]["download_bytes"] == 6000
    assert answer["totals"]["subjects"] == 1


def test_pseudonyms_for_the_anonymous_role(
    app: Any,
    api_config: Config,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: GoldenSite,
    now: list[datetime],
) -> None:
    with app_session_factory() as session:
        set_setting(session, "usage.pseudonymize_anonymous_role", True, updated_by="test")
        session.commit()
    store = SecretStore(
        api_config.secrets_dir / "secret.key", api_config.secrets_dir / "secrets.json"
    )
    if not (api_config.secrets_dir / "secret.key").exists():
        SecretStore.create_key(api_config.secrets_dir / "secret.key")
    store.set("usage.pseudonym_key", PSEUDONYM_KEY)
    with TestClient(app, base_url="http://testserver") as viewer:
        _signed_in(viewer, login_as, app_session_factory, "viewer")
        (row,) = _get(viewer, window="1")["rows"]
    token, label = pseudonym(PSEUDONYM_KEY.encode(), f"person:{site.ayse}")
    assert row["subject"] == {
        "kind": "person",
        "key": token,
        "label": label,
        "pseudonymous": True,
        "mac": None,
        "ip": None,
        "hostname": None,
    }


def test_access_needs_the_kvkk_notice(
    app: Any,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: GoldenSite,
    now: list[datetime],
) -> None:
    with TestClient(app, base_url="http://testserver") as hr:
        _signed_in(hr, login_as, app_session_factory, "hr", notice=False)
        refused = hr.get(API)
        assert (refused.status_code, refused.json()["error"]["code"]) == (
            403,
            "privacy_notice_required",
        )


@pytest.mark.parametrize("window", ["2", "0", "60", "abc", ""])
def test_another_window_is_refused(admin: TestClient, window: str) -> None:
    refused = admin.get(API, params={"window": window})
    assert refused.status_code == 422
    error = refused.json()["error"]
    assert error["code"] == "activity_window_invalid"
    assert error["message_tr"].startswith("Zaman aralığı geçersiz")
    assert error["message_en"].startswith("Invalid window")


def test_the_default_window_is_five_minutes(admin: TestClient) -> None:
    assert _get(admin)["window"] == 5


def test_the_view_is_audited_at_most_every_15_minutes(
    admin: TestClient, app_session_factory: sessionmaker[Session], now: list[datetime]
) -> None:
    """M3: at the first request, then not while polling, then again after 15 minutes; the
    target and the details name no subject."""

    def entries() -> list[AuditLog]:
        with app_session_factory() as session:
            return list(
                session.scalars(
                    select(AuditLog).where(AuditLog.action == "activity.view").order_by(AuditLog.id)
                )
            )

    for _ in range(3):
        _get(admin, window="5")
    first = entries()
    assert len(first) == 1
    assert first[0].target == "activity:devices"
    assert first[0].details == {"window": 5, "rows": 1}
    now[0] = first[0].time + timedelta(minutes=14)
    _get(admin, window="15")
    assert len(entries()) == 1
    now[0] = first[0].time + timedelta(minutes=15)
    _get(admin, window="15")
    later = entries()
    assert len(later) == 2
    assert later[1].details["window"] == 15
