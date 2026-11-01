"""``alerts.system``: ``R20-filter`` (Wave 4 contract 2.4) — opens on an expected day without
content-filter records, resolves when one arrives (or when the window is no longer expected), its
catalogue texts, and an e-mail that names no address."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.fmt import format_datetime
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, site_day
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, FgDns, FgWeb, Source
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts import system
from arcilogger_worker.alerts.mail import MailItem, render_html, render_text, subject
from arcilogger_worker.alerts.system import run_system_checks
from arcilogger_worker.alerts.texts import (
    DESCRIBERS,
    GENERIC,
    RULES,
    RuleText,
    describe,
    runbook,
)
from arcilogger_worker.testing import FIREWALL, tr

#: A week without a shipped holiday (as in test_compliance_filter.py).
FRIDAY = date(2026, 11, 6)
SATURDAY = date(2026, 11, 7)
SUNDAY = date(2026, 11, 8)
#: The last web filter record before the silence: Friday 17:00.
LAST = tr(FRIDAY, 17)
NOW = tr(SUNDAY, 12)
#: 29 October is in the shipped usage.holidays ("10-29"); 2026-10-30 is a Friday.
AFTER_HOLIDAY = tr(date(2026, 10, 30), 12)
KEY = "R20-filter:none"
URL = "https://arcilogger.example.com"
_lines = itertools.count(1)


@pytest.fixture(autouse=True)
def only_the_filter_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The content-filter check, as alerts.system runs it (own transaction)."""
    checks = tuple((name, check) for name, check in system.CHECKS if name == "filter")
    assert len(checks) == 1
    monkeypatch.setattr(system, "CHECKS", checks)


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


@pytest.fixture
def config(layout: Layout) -> Config:
    return Config(data_dir=layout.root, secrets_dir=layout.root)


@pytest.fixture
def check(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> Any:
    def run(now: datetime = NOW) -> dict[str, Alert]:
        db_session.commit()
        run_system_checks(sessions, layout, config, now=now)
        return active(db_session)

    return run


def active(session: Session) -> dict[str, Alert]:
    session.expire_all()
    rows = session.scalars(select(Alert).where(Alert.state.in_(("open", "acknowledged"))))
    return {row.dedup_key or "": row for row in rows}


def resolved(session: Session) -> Alert:
    session.expire_all()
    return session.scalars(
        select(Alert).where(Alert.dedup_key == KEY, Alert.state == "resolved")
    ).one()


def source(session: Session, name: str = "fortigate", address: str = "192.0.2.1") -> None:
    session.add(Source(name=name, type="fortigate", address=address))
    session.commit()


def _common(time: datetime, source_name: str = "fortigate") -> dict[str, object]:
    return {
        "time": time.astimezone(UTC),
        "time_received": time.astimezone(UTC),
        "source": source_name,
        "sender": FIREWALL,
        "raw_day": site_day(time),
        "raw_line": next(_lines),
        "raw_offset": 0,
    }


def web(session: Session, time: datetime, **columns: object) -> None:
    columns.setdefault("hostname", "www.example.com")
    columns.setdefault("profile", "default")
    session.add(FgWeb(**_common(time), **columns))
    session.commit()


def dns(session: Session, time: datetime) -> None:
    session.add(FgDns(**_common(time), qname="www.example.com"))
    session.commit()


def setting(session: Session, key: str, value: object) -> None:
    set_setting(session, key, value, updated_by="test")
    session.commit()


def test_r20_filter_opens_on_a_silent_expected_day(check: Any, db_session: Session) -> None:
    source(db_session)
    web(db_session, LAST)
    # Saturday 16:00: the last record is 23 hours old, inside the 24-hour window.
    assert check(tr(SATURDAY, 16)) == {}
    # Sunday 12:00, the defaults (every day expected, 24 hours): nothing for 43 hours.
    found = check()
    assert set(found) == {KEY}
    alert = found[KEY]
    assert (alert.rule, alert.severity, alert.state) == ("R20-filter", "high", "open")
    assert alert.title == "İçerik filtresi kaydı gelmiyor: son 24 saat"
    assert alert.details == {"hours": 24, "last_at": isoformat_site(LAST, "seconds")}
    # The next run keeps the one alert (dedup key).
    assert set(check(NOW + timedelta(minutes=5))) == {KEY}
    count = select(func.count()).select_from(Alert).where(Alert.rule == "R20-filter")
    assert db_session.scalar(count) == 1


def test_r20_filter_needs_a_fortigate_source_and_expected_days(
    check: Any, db_session: Session
) -> None:
    # No record at all, but no enabled, configured FortiGate source either: nothing expected.
    db_session.add(Source(name="fortigate-eski", type="fortigate", enabled=False))
    db_session.add(Source(name="unknown-192.0.2.10", type="fortigate", unknown=True))
    db_session.add(Source(name="core-r1", type="mikrotik", address="192.0.2.2"))
    assert check() == {}
    source(db_session)
    # Sunday taken out of the expected days: the window touches it, nothing opens.
    setting(db_session, "alerts.filter_days", [1, 2, 3, 4, 5, 6])
    assert check() == {}
    setting(db_session, "alerts.filter_days", [1, 2, 3, 4, 5, 6, 7])
    # A window that touches a listed holiday (29 October) is not expected, unless holidays count.
    assert check(AFTER_HOLIDAY) == {}
    setting(db_session, "alerts.filter_skip_holidays", False)
    found = check(AFTER_HOLIDAY)
    assert set(found) == {KEY}
    assert found[KEY].details == {"hours": 24, "last_at": None}  # no record within 8 days


def test_r20_filter_resolves_when_a_record_arrives(check: Any, db_session: Session) -> None:
    source(db_session)
    web(db_session, LAST)
    assert set(check()) == {KEY}
    dns(db_session, NOW + timedelta(minutes=30))  # either kind is enough
    assert check(NOW + timedelta(minutes=35)) == {}
    alert = resolved(db_session)
    assert alert.details["auto_resolved"]["reason"] == "condition_cleared"
    assert alert.resolved_at == NOW + timedelta(minutes=35)


def test_r20_filter_resolves_when_the_window_is_no_longer_expected(
    check: Any, db_session: Session
) -> None:
    """Contract 2.4: resolved whenever ``alert_due`` no longer holds — also when the window
    reaches a day that is not expected without any record; the next expected window opens a
    new alert."""
    source(db_session)
    web(db_session, LAST)
    assert set(check()) == {KEY}
    setting(db_session, "alerts.filter_days", [1, 2, 3, 4, 5])  # weekends not expected
    assert check(NOW + timedelta(minutes=5)) == {}
    assert resolved(db_session).details["auto_resolved"]["reason"] == "condition_cleared"
    # Tuesday 12:00: the window (Monday 12:00 to now) is expected again, still no record.
    found = check(tr(SUNDAY + timedelta(days=2), 12))
    assert set(found) == {KEY}
    assert found[KEY].details["hours"] == 24


def test_r20_filter_has_text_steps_and_describer() -> None:
    assert RULES["R20-filter"] == RuleText(
        "İçerik filtresi kaydı gelmiyor",
        "No content filter records",
        "Güvenlik duvarının web ve DNS filtresi kayıtlarını gönderdiğini denetleyin (Uyum Durumu, "
        "kart 7).",
        "Check that the firewall sends its web and DNS filter records (Uyum Durumu, card 7).",
    )
    assert runbook("R20-filter") == (
        "1. Uyum Durumu sayfasında 7. kartı açın: son kaydı ve beklenen günleri görün.\n"
        "2. Güvenlik duvarında web ve DNS filtresi kullanan politikalarda kayıt tutmanın açık "
        "olduğunu ve kayıtların ARCILOGGER'a gönderildiğini denetleyin (DEVICE-SETUP.md; sahada "
        "doğrulayın).\n"
        "3. Kurum bu günlerde çalışmıyorsa beklenen günleri ve tatil ayarını Ayarlar → Uygulama "
        "ayarları → Uyarılar ve e-posta'da düzeltin.\n"
        "4. Yeni bir içerik filtresi kaydı gelince uyarı kendiliğinden kapanır.",
        "1. Open card 7 on the Uyum Durumu page: see the last record and the expected days.\n"
        "2. Check on the firewall that the policies using web and DNS filtering log, and that the "
        "records are sent to ARCILOGGER (DEVICE-SETUP.md; verify on site).\n"
        "3. If the organisation does not work on these days, correct the expected days and the "
        "holiday setting in Settings → Application settings → Alerts and e-mail.\n"
        "4. The alert closes by itself when a new content filter record arrives.",
    )
    assert "R20-filter" in DESCRIBERS
    details = {"hours": 24, "last_at": "2026-11-06T17:00:00+03:00"}
    assert describe("R20-filter", details) == (
        "Son 24 saatte içerik filtresi kaydı yok.",
        "No content filter record in the last 24 hours.",
    )
    assert describe("R20-filter", {"hours": 48, "last_at": None})[1] == (
        "No content filter record in the last 48 hours."
    )
    # Only an integer number of hours is told.
    assert describe("R20-filter", {"hours": "24"}) == GENERIC
    assert describe("R20-filter", {"hours": True}) == GENERIC
    assert describe("R20-filter", {}) == GENERIC


def test_r20_filter_mail_names_no_address(check: Any, db_session: Session) -> None:
    source(db_session, "fortigate", "192.0.2.1")
    web(
        db_session,
        LAST,
        srcip="172.20.10.23",
        dstip="198.51.100.20",
        hostname="ornek-sunucu.example.com",
        url="/giris?kullanici=ali",
        profile="ornek-profil",
    )
    alert = check()[KEY]
    item = MailItem(
        alert.id, "new", alert.rule, alert.severity, alert.state, alert.time, None, alert.details
    )
    text = render_text([item], URL)
    page = render_html([item], URL)
    assert subject([item]) == "[ARCILOGGER] Yüksek: İçerik filtresi kaydı gelmiyor"
    for mail in (text, page):
        assert "R20-filter — İçerik filtresi kaydı gelmiyor" in mail
        assert "R20-filter — No content filter records" in mail
        assert "Son 24 saatte içerik filtresi kaydı yok." in mail
        assert "No content filter record in the last 24 hours." in mail
        assert RULES["R20-filter"].action_tr in mail
        assert RULES["R20-filter"].action_en in mail
    for hidden in (
        "192.0.2.1",
        "172.20.",
        FIREWALL,
        "198.51.100.",
        "fortigate",
        "ornek-sunucu",
        "kullanici",
        "ornek-profil",
        "@",
        format_datetime(LAST),
        alert.title,
    ):
        assert hidden not in text, hidden
        assert hidden not in page, hidden
