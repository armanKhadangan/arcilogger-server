"""Content-filter evidence (Wave 4 contract 2.4): counts per site day, expected days and holidays,
the quiet window, silent kinds and the web filter profiles."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import site_day
from arcilogger_db.kinds import kind_of
from arcilogger_db.models import FgDns, FgWeb, SearchCount, Source
from arcilogger_db.settings import DEFAULT_SETTINGS, set_setting
from arcilogger_worker.compliance_filter import FilterEvidence, FilterSettings, filter_evidence
from arcilogger_worker.testing import FIREWALL, tr

#: A Wednesday, 15:00 site time.
WEDNESDAY = date(2026, 10, 28)
NOW = tr(WEDNESDAY, 15)
#: 29 October is in the shipped usage.holidays ("10-29"); 2026-10-29 is a Thursday.
HOLIDAY = date(2026, 10, 29)
#: A week without a shipped holiday: Monday 2 November to Sunday 8 November 2026.
MONDAY = date(2026, 11, 2)
SATURDAY = date(2026, 11, 7)
SUNDAY = date(2026, 11, 8)
_lines = itertools.count(1)


def source(session: Session, name: str = "fortigate", **columns: object) -> None:
    session.add(Source(name=name, type=columns.pop("type", "fortigate"), **columns))
    session.commit()


def _common(time: datetime, source_name: str) -> dict[str, object]:
    return {
        "time": time.astimezone(UTC),
        "time_received": time.astimezone(UTC),
        "source": source_name,
        "sender": FIREWALL,
        "raw_day": site_day(time),
        "raw_line": next(_lines),
        "raw_offset": 0,
    }


def web(
    session: Session, time: datetime, profile: str | None = "default", source: str = "fortigate"
) -> None:
    session.add(FgWeb(**_common(time, source), hostname="www.example.com", profile=profile))
    session.commit()


def dns(session: Session, time: datetime, source: str = "fortigate") -> None:
    session.add(FgDns(**_common(time, source), qname="www.example.com"))
    session.commit()


def counted(
    session: Session, time: datetime, table: str, n: int, source: str = "fortigate"
) -> None:
    """``n`` lines of ``table`` in the hour of ``time`` (as the ingest loader counts them)."""
    subtype = {"fg_web": "webfilter", "fg_dns": "dns"}.get(table, "forward")
    session.add(
        SearchCount(
            hour=time.astimezone(UTC).replace(minute=0, second=0, microsecond=0),
            source=source,
            raw_day=site_day(time),
            kind=kind_of("utm" if table != "fg_traffic" else "traffic", subtype),
            tbl=table,
            action=None,
            net="172.20.10.0/24",
            n=n,
        )
    )
    session.commit()


def evidence(session: Session, now: datetime = NOW) -> FilterEvidence:
    result = filter_evidence(session, now=now)
    session.rollback()  # read only: nothing to commit
    return result


def test_counts_web_and_dns_per_day(db_session: Session) -> None:
    source(db_session, "fortigate")
    source(db_session, "fortigate-2")
    source(db_session, "fortigate-eski", enabled=False)
    source(db_session, "unknown-192.0.2.10", unknown=True)
    source(db_session, "core-r1", type="mikrotik")
    yesterday = WEDNESDAY - timedelta(days=1)
    counted(db_session, tr(WEDNESDAY, 8, 10), "fg_web", 5)
    counted(db_session, tr(WEDNESDAY, 9, 59), "fg_web", 7, source="fortigate-2")
    counted(db_session, tr(WEDNESDAY, 14, 30), "fg_dns", 11)
    # Site midnight: the hour 21:00 UTC is today's first hour, 20:00 UTC yesterday's last.
    counted(db_session, tr(WEDNESDAY, 0, 0), "fg_dns", 2)
    counted(db_session, tr(yesterday, 23, 59), "fg_web", 3)
    counted(db_session, tr(WEDNESDAY - timedelta(days=6), 12), "fg_web", 4)
    # Older than the 7 days shown (still within the 8 days looked at).
    counted(db_session, tr(WEDNESDAY - timedelta(days=7), 12), "fg_web", 100)
    # Not counted: other tables, disabled, unknown and non-FortiGate sources.
    counted(db_session, tr(WEDNESDAY, 10), "fg_traffic", 1000)
    counted(db_session, tr(WEDNESDAY, 10), "fg_web", 1000, source="fortigate-eski")
    counted(db_session, tr(WEDNESDAY, 10), "fg_dns", 1000, source="unknown-192.0.2.10")
    counted(db_session, tr(WEDNESDAY, 10), "fg_web", 1000, source="core-r1")
    web(db_session, NOW - timedelta(hours=2))
    dns(db_session, NOW - timedelta(hours=1))
    dns(db_session, NOW - timedelta(days=9))  # outside the 8 days

    found = evidence(db_session)

    assert found.applies
    assert found.today == {"fg_web": 12, "fg_dns": 13}
    assert [day for day, _, _ in found.days] == [
        WEDNESDAY - timedelta(days=back) for back in range(7)
    ]
    assert found.days[0] == (WEDNESDAY, 12, 13)
    assert found.days[1] == (yesterday, 3, 0)
    assert found.days[6] == (WEDNESDAY - timedelta(days=6), 4, 0)
    assert all(row[1:] == (0, 0) for row in found.days[2:6])
    assert found.last_at == NOW - timedelta(hours=1)
    assert found.window_hours == 24
    assert found.window_expected
    assert not found.alert_due
    assert found.silent_kinds == ()


def test_every_day_of_the_week_is_expected_by_default(db_session: Session) -> None:
    settings = FilterSettings.load(db_session)
    assert settings.days == (1, 2, 3, 4, 5, 6, 7)
    assert settings.skip_holidays
    assert settings.quiet_hours == 24
    week = [MONDAY + timedelta(days=n) for n in range(7)]
    assert [day.isoweekday() for day in week] == [1, 2, 3, 4, 5, 6, 7]
    assert all(settings.expected(day) for day in week)

    source(db_session)
    web(db_session, tr(SATURDAY - timedelta(days=1), 17))  # Friday evening, then nothing
    for now in (tr(SATURDAY, 20), tr(SUNDAY, 12)):  # Saturday and Sunday are expected too
        found = evidence(db_session, now)
        assert found.window_expected, now
        assert found.alert_due, now
        assert found.last_at == tr(SATURDAY - timedelta(days=1), 17)
    # Within the 24 hours after the last record: no alert yet.
    assert not evidence(db_session, tr(SATURDAY, 16, 59)).alert_due


def test_a_day_removed_from_filter_days_is_not_expected(db_session: Session) -> None:
    set_setting(db_session, "alerts.filter_days", [1, 2, 3, 4, 5, 6], updated_by="tolga")
    db_session.commit()
    settings = FilterSettings.load(db_session)
    assert not settings.expected(SUNDAY)
    assert settings.expected(SATURDAY)
    assert settings.expected(SUNDAY + timedelta(days=1))

    source(db_session)
    web(db_session, tr(SATURDAY, 8))
    # Sunday noon: the window (Saturday noon - Sunday noon) touches Sunday.
    sunday = evidence(db_session, tr(SUNDAY, 12))
    assert not sunday.window_expected
    assert not sunday.alert_due
    # Monday before midnight + 24 h: the window still starts on Sunday.
    monday = SUNDAY + timedelta(days=1)
    assert not evidence(db_session, tr(monday, 23, 59, 59)).alert_due
    # Tuesday 00:00: the window is Monday alone.
    tuesday = evidence(db_session, tr(monday + timedelta(days=1), 0))
    assert tuesday.window_expected
    assert tuesday.alert_due

    # No day at all: content-filter records are never expected.
    set_setting(db_session, "alerts.filter_days", [], updated_by="tolga")
    db_session.commit()
    nothing = evidence(db_session, tr(monday + timedelta(days=1), 0))
    assert not nothing.window_expected
    assert not nothing.alert_due


def test_listed_holidays_are_not_expected_by_default(db_session: Session) -> None:
    assert "10-29" in DEFAULT_SETTINGS["usage.holidays"]
    holidays = [*DEFAULT_SETTINGS["usage.holidays"], "2026-11-04"]
    set_setting(db_session, "usage.holidays", holidays, updated_by="tolga")
    db_session.commit()
    settings = FilterSettings.load(db_session)
    assert not settings.expected(HOLIDAY)  # "MM-DD": every year
    assert not settings.expected(date(2027, 10, 29))
    assert not settings.expected(date(2026, 11, 4))  # "YYYY-MM-DD": once
    assert settings.expected(date(2027, 11, 4))
    assert settings.expected(HOLIDAY - timedelta(days=1))

    source(db_session)
    web(db_session, tr(HOLIDAY - timedelta(days=1), 12))
    # Any window that touches 29 October is not expected.
    for now in (tr(HOLIDAY, 18), tr(HOLIDAY + timedelta(days=1), 23, 59)):
        found = evidence(db_session, now)
        assert not found.window_expected, now
        assert not found.alert_due, now
    found = evidence(db_session, tr(HOLIDAY + timedelta(days=2), 0))
    assert found.window_expected
    assert found.alert_due


def test_holidays_count_when_skipping_is_off(db_session: Session) -> None:
    set_setting(db_session, "alerts.filter_skip_holidays", False, updated_by="tolga")
    db_session.commit()
    settings = FilterSettings.load(db_session)
    assert not settings.skip_holidays
    assert settings.expected(HOLIDAY)

    source(db_session)
    web(db_session, tr(HOLIDAY - timedelta(days=1), 12))
    found = evidence(db_session, tr(HOLIDAY, 18))
    assert found.window_expected
    assert found.alert_due


def test_alert_due_only_when_the_whole_window_is_expected(db_session: Session) -> None:
    set_setting(db_session, "alerts.filter_days", [1, 2, 3, 4, 5], updated_by="tolga")
    db_session.commit()
    source(db_session)
    friday = SATURDAY - timedelta(days=1)
    monday = SUNDAY + timedelta(days=1)
    tuesday = monday + timedelta(days=1)
    web(db_session, tr(friday, 17))

    # A window that touches the weekend is not expected, whatever is missing in it.
    for now in (tr(SATURDAY, 20), tr(SUNDAY, 20), tr(monday, 10), tr(monday, 23, 59, 59)):
        found = evidence(db_session, now)
        assert not found.window_expected, now
        assert not found.alert_due, now
    found = evidence(db_session, tr(tuesday, 0))
    assert found.window_expected
    assert found.alert_due

    # A record in the window (its first moment included) keeps the alert away.
    dns(db_session, tr(monday, 0))
    assert not evidence(db_session, tr(tuesday, 0)).alert_due
    assert evidence(db_session, tr(tuesday, 0, 0, 1)).alert_due

    # A shorter window lies within Monday.
    set_setting(db_session, "alerts.filter_quiet_hours", 6, updated_by="tolga")
    db_session.commit()
    found = evidence(db_session, tr(monday, 10))
    assert found.window_hours == 6
    assert found.window_expected
    assert found.alert_due
    assert not evidence(db_session, tr(monday, 5, 59)).alert_due  # starts on Sunday


def test_a_silent_kind_is_reported(db_session: Session) -> None:
    source(db_session)
    web(db_session, NOW - timedelta(hours=1))
    dns(db_session, NOW - timedelta(days=3))
    found = evidence(db_session)
    assert found.silent_kinds == ("dns",)
    assert not found.alert_due  # the other kind still arrives
    assert found.last_at == NOW - timedelta(hours=1)

    # Both silent: no record in the window at all (Saturday 31 October, after the holiday).
    later = NOW + timedelta(days=3)
    found = evidence(db_session, later)
    assert found.silent_kinds == ("web", "dns")
    assert found.alert_due

    # A kind not seen in the last 7 site days is not "silent" (it never came lately). The oldest
    # day shown starts 6 days 15 hours before NOW.
    db_session.execute(delete(FgDns))
    dns(db_session, NOW - timedelta(days=6, hours=16))
    db_session.commit()
    assert evidence(db_session).silent_kinds == ()
    dns(db_session, NOW - timedelta(days=6, hours=14))
    assert evidence(db_session).silent_kinds == ("dns",)


LOAD_WEB = text(
    "INSERT INTO fg_web (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "profile) SELECT t, t, 'fortigate', CAST(:sender AS inet), :day, g, 0, "
    "CASE WHEN g = 1 THEN NULL WHEN g % 2 = 0 THEN 'personel' ELSE 'misafir' END "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:end AS timestamptz) - g * interval '100 milliseconds' AS t) AS x"
)


def test_profiles_come_from_the_newest_rows_only(db_session: Session) -> None:
    source(db_session)
    # The newest 10 000 rows (one without a profile), then one older row within the 24 hours and
    # one older than 24 hours.
    db_session.execute(
        LOAD_WEB,
        {"sender": FIREWALL, "day": WEDNESDAY, "rows": 10_000, "end": NOW - timedelta(minutes=1)},
    )
    db_session.commit()
    web(db_session, NOW - timedelta(hours=2), profile="eski")
    web(db_session, NOW - timedelta(hours=25), profile="dun")
    assert evidence(db_session).web_profiles == ("misafir", "personel")

    # One row fewer among the newest: the older row of the 24 hours is among them now.
    newest = db_session.scalar(select(FgWeb.id).order_by(FgWeb.time.desc()).limit(1))
    db_session.execute(delete(FgWeb).where(FgWeb.id == newest))
    db_session.commit()
    assert evidence(db_session).web_profiles == ("eski", "misafir", "personel")

    # Only the last 24 hours, whatever the number of rows.
    db_session.execute(delete(FgWeb).where(FgWeb.time > NOW - timedelta(hours=24)))
    db_session.commit()
    assert evidence(db_session).web_profiles == ()


def test_without_a_fortigate_source_it_does_not_apply(db_session: Session) -> None:
    source(db_session, "core-r1", type="mikrotik")
    source(db_session, "fortigate-eski", enabled=False)
    source(db_session, "unknown-192.0.2.10", unknown=True)
    source(db_session, "genel", type="generic")
    counted(db_session, tr(WEDNESDAY, 10), "fg_web", 5, source="fortigate-eski")
    counted(db_session, tr(WEDNESDAY, 10), "fg_dns", 5, source="unknown-192.0.2.10")
    web(db_session, NOW - timedelta(days=2), source="fortigate-eski")

    found = evidence(db_session)
    assert not found.applies
    assert found.window_expected
    assert not found.alert_due  # nothing expected without a FortiGate source
    assert found.today == {"fg_web": 0, "fg_dns": 0}
    assert all(row[1:] == (0, 0) for row in found.days)

    source(db_session, "fortigate")
    found = evidence(db_session)
    assert found.applies
    assert found.alert_due
