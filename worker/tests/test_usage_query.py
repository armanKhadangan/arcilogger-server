"""The usage queries (``arcilogger_worker.usage.query``) on the golden usage site.

The figures are the hand-computed ones of test_usage_golden.py (14.10.2026). Ayşe's site
``Bilinmiyor`` traffic: posta.example (09:02), windowsupdate.com (09:58, 09:59),
reklam.example and 8.8.8.8 in the same minute (10:30), Microsoft-Azure (10:59) — raw times
60 + 120 + 60 + 60 + 60 = 360 s over only 5 minutes (300 s) with traffic.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.models import UsageBlocked, UsageDeviceDaily
from arcilogger_db.settings import set_setting
from arcilogger_worker.testing import tr
from arcilogger_worker.usage import query
from arcilogger_worker.usage.jobs import rebuild_day
from arcilogger_worker.usage.query import Context, Period
from arcilogger_worker.usage.testing import DAY, NEXT, PC1_MAC, GoldenSite, build_golden_site

NOW = tr(NEXT + timedelta(days=1), 9)


@pytest.fixture
def site(db_session: Session, layout: Layout) -> GoldenSite:
    golden = build_golden_site(db_session, layout)
    rebuild_day(db_session, DAY, now=NOW)
    rebuild_day(db_session, NEXT, now=NOW)
    return golden


def _context(session: Session) -> Context:
    return Context(session, Period(DAY, DAY))


def test_per_device_blocked_attempts_count_every_attempt(
    db_session: Session, site: GoldenSite
) -> None:
    """``usage.blocked_per_day`` limits the stored attempts, never the per-device count."""
    set_setting(db_session, "usage.blocked_per_day", 1, updated_by="test")
    db_session.commit()
    rebuild_day(db_session, DAY, now=NOW)
    ayse = f"person:{site.ayse}"
    stored = db_session.scalar(
        select(func.count()).select_from(UsageBlocked).where(UsageBlocked.subject == ayse)
    )
    assert stored == 1  # only the newest attempt is kept as a row
    blocked = db_session.scalar(
        select(func.sum(UsageDeviceDaily.blocked)).where(
            UsageDeviceDaily.subject == ayse, UsageDeviceDaily.mac == PC1_MAC
        )
    )
    assert blocked == 3
    detail = query.subject_detail(_context(db_session), ayse)
    assert detail["metrics"]["blocked"] == 3
    assert len(detail["blocked"]) == 1
    (device,) = detail["per_device"]
    assert device["metrics"]["blocked"] == 3  # adds up to the person's total


def test_site_users_have_the_sites_figures_only(db_session: Session, site: GoldenSite) -> None:
    ctx = _context(db_session)
    detail = query.site_detail(ctx, "video.example")
    assert detail is not None
    (user,) = detail["users"]
    assert user["subject"]["label"] == "Ayşe Yılmaz"
    assert user["metrics"] == {
        "online_seconds": 300,  # the raw time: 09:00-09:04
        "active_seconds": 210,
        "download_bytes": 18600,
        "upload_bytes": 1860,
        "sessions": 1,
        "blocked": 0,
        "off_hours_active_seconds": 0,
        "first_activity": None,
        "last_activity": None,
    }
    assert user["top_sites"] == [{"domain": "video.example", "seconds": 210}]
    assert detail["users_total"] == 1


def test_category_users_never_sum_the_raw_times_of_its_sites(
    db_session: Session, site: GoldenSite
) -> None:
    ctx = _context(db_session)
    detail = query.category_detail(ctx, "Bilinmiyor")
    assert detail is not None
    users = {row["subject"]["label"]: row for row in detail["users"]}
    ayse = users["Ayşe Yılmaz"]["metrics"]
    # 360 s of raw time over 5 minutes is no online time: the category has none.
    assert ayse["online_seconds"] == 0
    assert ayse["active_seconds"] == 30  # posta.example; the rest is background
    assert ayse["download_bytes"] == 3000 + 600000 + 900 + 5000 + 50000
    assert ayse["sessions"] == 5
    assert users["Ayşe Yılmaz"]["top_sites"] == [{"domain": "posta.example", "seconds": 30}]
    assert set(users) == {"Ayşe Yılmaz", "172.20.41.77"}
    assert detail["users_total"] == 2
    assert detail["sites_total"] == len(detail["sites"]) == 6


def test_detail_lists_are_capped_unless_asked_for_whole(
    db_session: Session, site: GoldenSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(query, "USERS_MAX", 1)
    monkeypatch.setattr(query, "CATEGORY_SITES_MAX", 2)
    ctx = _context(db_session)
    capped = query.site_detail(ctx, "portal.example")
    assert capped is not None
    assert [row["subject"]["label"] for row in capped["users"]] == ["Burak Demir"]
    assert capped["users_total"] == 2
    whole = query.site_detail(ctx, "portal.example", capped=False)
    assert whole is not None
    assert [row["subject"]["label"] for row in whole["users"]] == ["Burak Demir", "Can Öztürk"]
    assert whole["users_total"] == 2
    unknown = query.category_detail(ctx, "Bilinmiyor")
    assert unknown is not None
    assert (len(unknown["sites"]), unknown["sites_total"]) == (2, 6)
    assert (len(unknown["users"]), unknown["users_total"]) == (1, 2)
    every = query.category_detail(ctx, "Bilinmiyor", capped=False)
    assert every is not None
    assert (len(every["sites"]), len(every["users"])) == (6, 2)
