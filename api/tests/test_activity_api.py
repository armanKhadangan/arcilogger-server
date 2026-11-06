"""``GET /api/v1/activity/devices`` (Wave 5 contract §6.2 M2, M3; tests §6.5 W5-M) on the golden
usage site (:mod:`arcilogger_worker.usage.testing`, 14.10.2026), answered from the worker's
snapshot (``state/activity.json``, :mod:`arcilogger_worker.activity`).

The snapshot is written as the worker writes it, at a "now" of the golden day: only the logs
before "now" are read (the golden day's later logs are in the database already). The route's
clock is set to the same "now".
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
from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore
from arcilogger_db.models import AuditLog, Person
from arcilogger_db.settings import set_setting
from arcilogger_worker.activity import Snapshot
from arcilogger_worker.testing import tr
from arcilogger_worker.usage.pseudonyms import pseudonym
from arcilogger_worker.usage.testing import DAY, GoldenSite, build_golden_site

API = "/api/v1/activity/devices"
Login = Callable[..., LoggedIn]
PSEUDONYM_KEY = "golden-activity-pseudonym-key-0123456789"  # gitleaks:allow (test-only key)
#: 09:05:30: the 09:00-09:05 video session has ended (interim logs at 09:02 and 09:04, the
#: final one at 09:05), the mail session of 09:02-09:03 too.
NOW = tr(DAY, 9, 5, 30)


def write_snapshot(session: Session, layout: Layout, now: datetime) -> dict[str, Any]:
    """The worker's snapshot at ``now``: its ticks until the warm-up has covered the hour."""
    snapshot = Snapshot(layout.activity)
    for _ in range(200):
        details = snapshot.tick(session, now)
        if not details["warming_up"]:
            return details
    raise AssertionError("the snapshot never covered the hour")


@pytest.fixture
def site(db_session: Session, layout: Layout) -> GoldenSite:
    golden = build_golden_site(db_session, layout)
    db_session.commit()
    write_snapshot(db_session, layout, NOW)
    return golden


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


def _ayse(answer: Any, site: GoldenSite) -> dict[str, Any]:
    (row,) = [row for row in answer["rows"] if row["subject"]["key"] == f"person:{site.ayse}"]
    return row  # type: ignore[no-any-return]


def test_an_interim_delta_counts_at_its_own_minute(
    admin: TestClient, db_session: Session, layout: Layout, now: list[datetime]
) -> None:
    """§6.5 (X-B1): at 09:02:30 only the video session's first interim log has come (09:02,
    12 000 / 1 200 bytes over its 120 s): its bytes are in the minutes 09:00 and 09:01 while the
    session is still open, before its next interim log and its final log."""
    early = tr(DAY, 9, 2, 30)
    write_snapshot(db_session, layout, early)
    now[0] = early.astimezone(UTC)
    answer = _get(admin, window="5")
    assert _minute(answer, 9, 0) == (6000, 600)
    assert _minute(answer, 9, 1) == (6000, 600)
    assert _minute(answer, 8, 59) == (0, 0)
    assert len(answer["per_minute"]) == 60
    assert datetime.fromisoformat(answer["data_until"]) == tr(DAY, 9, 2).astimezone(UTC)
    assert answer["warming_up"] is False


def test_the_window_lists_its_subjects(admin: TestClient, site: GoldenSite) -> None:
    """09:01-09:06: the video session's 6 000 / 600 of 09:01, its second interval (6 000 / 600
    over 09:02-09:04) and its final rest (600 / 60, 09:04-09:05), and the mail session named by
    DNS (3 000 / 300, started 09:02, the window's only start); rates over 300 s."""
    answer = _get(admin, window="5")
    assert answer["window"] == 5
    row = _ayse(answer, site)
    assert row["subject"]["label"] == "Ayşe Yılmaz"
    assert (row["download_bytes"], row["upload_bytes"]) == (15600, 1560)
    assert row["download_bps"] == pytest.approx(15600 * 8 / 300)
    assert row["sessions"] == 1
    assert {(d["domain"], d["bytes"]) for d in row["top_domains"]} == {
        ("video.example", 13860),
        ("posta.example", 3300),
    }
    assert datetime.fromisoformat(row["last_seen"]) == tr(DAY, 9, 5).astimezone(UTC)
    assert answer["more"] is False
    assert answer["totals"]["download_bytes"] >= 15600
    assert answer["warming_up"] is False


def test_without_a_snapshot_the_answer_is_empty_and_warming_up(
    admin: TestClient, layout: Layout
) -> None:
    layout.activity.unlink()
    answer = _get(admin, window="5")
    assert (answer["rows"], answer["warming_up"], answer["data_until"]) == ([], True, None)
    assert len(answer["per_minute"]) == 60


def test_an_excluded_person_is_no_row_but_counts_in_the_totals(
    admin: TestClient, app_session_factory: sessionmaker[Session], site: GoldenSite
) -> None:
    before = _get(admin, window="5")
    with app_session_factory() as session:
        session.execute(update(Person).where(Person.id == site.ayse).values(usage_excluded=True))
        session.commit()
    after = _get(admin, window="5")
    assert f"person:{site.ayse}" not in {row["subject"]["key"] for row in after["rows"]}
    assert after["totals"] == before["totals"]


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
        rows = _get(viewer, window="5")["rows"]
    (row,) = [row for row in rows if row["subject"]["kind"] == "person"]
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

    answers = [_get(admin, window="5") for _ in range(3)]
    first = entries()
    assert len(first) == 1
    assert first[0].target == "activity:devices"
    assert first[0].details == {"window": 5, "rows": len(answers[0]["rows"])}
    now[0] = first[0].time + timedelta(minutes=14)
    _get(admin, window="15")
    assert len(entries()) == 1
    now[0] = first[0].time + timedelta(minutes=15)
    _get(admin, window="15")
    later = entries()
    assert len(later) == 2
    assert later[1].details["window"] == 15


def test_an_old_snapshot_is_warming_up(admin: TestClient, now: list[datetime]) -> None:
    """The worker rewrites a quiet site's snapshot when the minute rolls: a minute and a tick
    old is current; after 2 minutes (the worker stopped) the figures are flagged as incomplete."""
    written = NOW.astimezone(UTC)  # the fixture's snapshot was written at NOW
    now[0] = written + timedelta(seconds=90)
    assert _get(admin, window="5")["warming_up"] is False
    now[0] = written + timedelta(seconds=121)
    assert _get(admin, window="5")["warming_up"] is True
