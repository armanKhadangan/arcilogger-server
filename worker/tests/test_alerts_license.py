"""``R20-license``: the licence alerts of ``license.check`` — every condition with a positive and a
negative case, its automatic resolution, one alert per occurrence (a person's closing sticks), the
trusted time deciding, the job never failing because of them, and the e-mail's whitelisted facts.

Statuses come from the real :func:`~arcilogger_core.license.state.evaluate` on controlled inputs
(licences signed with the test key); :func:`check` does what ``license.check`` does — the licence
events first, then the alerts.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.license.state import Installation, LicenseInputs, LicenseStatus, evaluate
from arcilogger_core.license.testing import (
    TEST_INSTALLATION_ID,
    fixed_identity,
    make_license,
    tamper,
    trusted_test_keys,
)
from arcilogger_core.license.trusted_time import TrustedTime, clear_cache, site_day_start
from arcilogger_core.timeutil import isoformat_site, utc_now
from arcilogger_db import licensing
from arcilogger_db.models import Alert, AlertEvent, JobRun, LicenseEvent, LicenseInstallation
from arcilogger_worker import licensing as worker_licensing
from arcilogger_worker.alerts.license import check_license
from arcilogger_worker.alerts.mail import MailItem, build_message
from arcilogger_worker.alerts.store import change_state
from arcilogger_worker.alerts.texts import GENERIC, describe, rule_text
from arcilogger_worker.service import Worker, default_jobs

pytestmark = pytest.mark.license_live

SERVER = "XW68-7K1Z-5FGV-AGT8"  # the server id of fixed_identity()
OTHER = "0000-1111-2222-3333"
#: 20 October 2026, 12:00 site time.
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
RELEASE = date(2026, 10, 1)
PAID = ["usage_analytics", "usage_reports", "bulk_import"]
NAMES_TR = "Kullanım analizi, Zamanlanmış kullanım raporları, Toplu içe aktarma (CSV)"
NAMES_EN = "Usage analytics, Scheduled usage reports, Bulk import (CSV)"
DAY = timedelta(days=1)
#: A person, a raw FortiGate line and an address: none of it may reach an alert e-mail.
PERSON = "Ayşe Yılmaztürk"
RAW = '<189>date=2026-10-19 srcip=172.20.42.57 user="ayse.yilmazturk" dstip=203.0.113.10'


@pytest.fixture(autouse=True)
def _test_key() -> Iterator[None]:
    with trusted_test_keys():
        yield


def trusted(at: datetime, *, floor: datetime | None = None) -> TrustedTime:
    """The trusted time at ``at``; ``floor``: the end of a newer vault day (a clock set back)."""
    return TrustedTime(
        now=max(at, floor) if floor else at,
        system=at,
        tsa=None,
        tsa_day=None,
        vault_day_end=floor,
        release_floor=site_day_start(RELEASE),
        license_issued=None,
    )


def status(
    *,
    at: datetime = NOW,
    started: datetime = NOW,
    stored: bytes | None = None,
    server_id: str = SERVER,
    users: int = 2,
    sources: int = 3,
    unconfigured: int = 0,
    floor: datetime | None = None,
) -> LicenseStatus:
    return evaluate(
        LicenseInputs(
            installation=Installation(TEST_INSTALLATION_ID, started),
            stored=stored,
            server_id=server_id,
            server_id_error=None,
            trusted=trusted(at, floor=floor),
            lower_bounds={},
            users_used=users,
            log_sources_used=sources,
            log_sources_unconfigured=unconfigured,
            version="0.1.0",
            release_date=RELEASE,
        )
    )


def licence(**kwargs: Any) -> bytes:
    kwargs.setdefault("server_ids", [SERVER])
    kwargs.setdefault("modules", ("core_5651", *PAID))
    return make_license(**kwargs)


def check(session: Session, current: LicenseStatus, at: datetime = NOW) -> dict[str, Any]:
    """What ``license.check`` does: the licence events, then the alerts."""
    licensing.check_and_record(session, current, audit=lambda *_: None, clock=lambda: at)
    report = check_license(session, current, now=at)
    session.commit()
    return report


def active(session: Session) -> dict[str, Alert]:
    session.expire_all()
    rows = session.scalars(select(Alert).where(Alert.state.in_(("open", "acknowledged"))))
    return {row.dedup_key or "": row for row in rows}


def closed(session: Session, key: str) -> list[Alert]:
    session.expire_all()
    return list(
        session.scalars(
            select(Alert)
            .where(Alert.dedup_key == key, Alert.state == "resolved")
            .order_by(Alert.id)
        )
    )


def reason(alert: Alert) -> str:
    return str(alert.details["auto_resolved"]["reason"])


def newest_state_event(session: Session) -> LicenseEvent:
    event = session.scalars(
        select(LicenseEvent).where(LicenseEvent.kind == "state").order_by(LicenseEvent.id.desc())
    ).first()
    assert event is not None
    return event


def dismiss(session: Session, alert: Alert, at: datetime) -> None:
    change_state(
        session, alert, "dismissed", by="admin", user_id=None, now=at, audit=lambda *_: None
    )
    session.commit()


# -- the trial ends -------------------------------------------------------------------------------


def test_the_trial_ending_within_three_days(db_session: Session) -> None:
    started = NOW - 10 * DAY  # 4 days left at NOW
    assert check(db_session, status(started=started))["found"] == []  # negative
    assert active(db_session) == {}

    report = check(db_session, status(at=NOW + DAY, started=started), NOW + DAY)
    assert report["opened"] == ["R20-license:trial-ending"]
    alert = active(db_session)["R20-license:trial-ending"]
    ends = isoformat_site(started + 14 * DAY, "seconds")
    assert (alert.rule, alert.severity) == ("R20-license", "medium")
    assert alert.title == "Deneme süresi bitiyor: 3 gün kaldı (24.10.2026 12:00:00)"
    assert alert.details == {
        "case": "trial_ending",
        "days_left": 3,
        "until": ends,
        "modules": PAID,
        "occurrence": ends,
        "title_en": f"Trial ending: 3 day(s) left ({ends})",
    }
    # One alert, brought up to date day by day.
    check(db_session, status(at=NOW + 2 * DAY, started=started), NOW + 2 * DAY)
    alerts = active(db_session)
    assert list(alerts) == ["R20-license:trial-ending"]
    assert alerts["R20-license:trial-ending"].details["days_left"] == 2

    # The trial is over: the warning is resolved, the paid modules are off.
    over = NOW + 5 * DAY
    report = check(db_session, status(at=over, started=started), over)
    assert report["resolved"] == ["R20-license:trial-ending"]
    assert report["opened"] == ["R20-license:off"]
    (ended,) = closed(db_session, "R20-license:trial-ending")
    assert reason(ended) == "license_changed"
    off = active(db_session)["R20-license:off"]
    assert (off.severity, off.details["case"], off.details["modules"]) == (
        "medium",
        "trial_expired",
        PAID,
    )
    assert off.title == "Deneme süresi doldu: ücretli modüller kapalı"
    assert off.details["title_en"] == "The trial has ended: paid modules are off"
    assert off.details["occurrence"] == f"license_events:{newest_state_event(db_session).id}"


def test_a_licence_installed_during_the_trial_resolves_the_warning(db_session: Session) -> None:
    started = NOW - 12 * DAY
    check(db_session, status(started=started))
    assert "R20-license:trial-ending" in active(db_session)
    report = check(db_session, status(started=started, stored=licence()))
    assert report["resolved"] == ["R20-license:trial-ending"]
    assert reason(closed(db_session, "R20-license:trial-ending")[0]) == "licensed"
    assert active(db_session) == {}


def test_the_trusted_time_decides_not_the_workers_clock(db_session: Session) -> None:
    """The server clock set back 4 days: the newest vault day says the trial is over."""
    started = NOW - 12 * DAY
    behind = status(at=NOW - 4 * DAY, started=started, floor=NOW)
    assert behind.state == "trial"  # 2 days left on the trusted time, 6 on the clock
    check(db_session, behind, NOW - 4 * DAY)
    assert active(db_session)["R20-license:trial-ending"].details["days_left"] == 2
    over = status(at=NOW - 4 * DAY, started=started, floor=NOW + 3 * DAY)
    assert over.state == "trial_expired"
    check(db_session, over, NOW - 4 * DAY)
    assert list(active(db_session)) == ["R20-license:off"]


# -- a time-limited licence ends -------------------------------------------------------------------


def test_a_time_limited_licence_ending_within_14_days(db_session: Session) -> None:
    stored = licence(valid_until=date(2026, 11, 4))  # 15 days left at NOW
    assert check(db_session, status(stored=stored))["found"] == []
    report = check(db_session, status(at=NOW + DAY, stored=stored), NOW + DAY)
    assert report["opened"] == ["R20-license:expiring"]
    alert = active(db_session)["R20-license:expiring"]
    assert alert.severity == "medium"
    assert alert.title == "Lisansın süresi bitiyor: 04.11.2026 (14 gün kaldı)"
    assert {k: alert.details[k] for k in ("case", "days_left", "until", "modules")} == {
        "case": "expiring",
        "days_left": 14,
        "until": "2026-11-04",
        "modules": PAID,
    }
    last_day = NOW + 15 * DAY
    check(db_session, status(at=last_day, stored=stored), last_day)
    assert active(db_session)["R20-license:expiring"].details["days_left"] == 0
    # Renewed: a licence without a time limit.
    report = check(db_session, status(at=last_day, stored=licence()), last_day)
    assert report["resolved"] == ["R20-license:expiring"]
    assert reason(closed(db_session, "R20-license:expiring")[0]) == "licensed"


def test_an_expiring_licence_that_ran_out_turns_into_paid_modules_off(db_session: Session) -> None:
    stored = licence(valid_until=date(2026, 10, 25))
    check(db_session, status(stored=stored))
    assert list(active(db_session)) == ["R20-license:expiring"]
    after = NOW + 6 * DAY
    report = check(db_session, status(at=after, stored=stored), after)
    assert (report["opened"], report["resolved"]) == (
        ["R20-license:off"],
        ["R20-license:expiring"],
    )
    off = active(db_session)["R20-license:off"]
    assert (off.details["case"], off.details["until"]) == ("expired", "2026-10-25")
    assert off.title == "Lisansın süresi 25.10.2026 tarihinde doldu: ücretli modüller kapalı"


# -- paid modules off -----------------------------------------------------------------------------


#: name: (the status, severity, ``details.case``, ``details.until``).
LOCKED: dict[str, tuple[Callable[[], LicenseStatus], str, str, str | None]] = {
    "trial_expired": (lambda: status(started=NOW - 30 * DAY), "medium", "trial_expired", None),
    "trial_clock_inconsistent": (
        lambda: status(started=NOW + timedelta(hours=1)),
        "medium",
        "trial_clock_inconsistent",
        None,
    ),
    "invalid": (
        lambda: status(stored=tamper(licence(), "licensee", "X")),
        "high",
        "invalid",
        None,
    ),
    "wrong_server": (
        lambda: status(stored=licence(), server_id=OTHER),
        "high",
        "wrong_server",
        None,
    ),
    "expired": (
        lambda: status(stored=licence(valid_until=date(2026, 10, 19))),
        "medium",
        "expired",
        "2026-10-19",
    ),
    "version_not_covered": (
        lambda: status(stored=licence(maintenance_until=date(2026, 9, 1))),
        "medium",
        "version_not_covered",
        "2026-09-01",
    ),
}


def locked(name: str) -> LicenseStatus:
    return LOCKED[name][0]()


@pytest.mark.parametrize("name", sorted(LOCKED))
def test_paid_modules_off_in_every_locked_state(db_session: Session, name: str) -> None:
    _, severity, case, until = LOCKED[name]
    current = locked(name)
    assert current.state == ("trial_expired" if name.startswith("trial") else name)
    report = check(db_session, current)
    assert report["opened"] == ["R20-license:off"]
    alert = active(db_session)["R20-license:off"]
    assert alert.severity == severity
    assert (alert.details["case"], alert.details["until"]) == (case, until)
    assert alert.details["state"] == current.state
    assert alert.details["modules"] == PAID
    sentence_tr, sentence_en = describe("R20-license", alert.details)
    assert f"ücretli modüller kapalı ({NAMES_TR})" in sentence_tr
    assert f"the paid modules are off ({NAMES_EN})" in sentence_en
    # Licensed again: resolved by itself.
    report = check(db_session, status(stored=licence()))
    assert report["resolved"] == ["R20-license:off"]
    assert reason(closed(db_session, "R20-license:off")[0]) == "licensed"
    assert active(db_session) == {}


def test_nothing_while_licensed_or_when_no_paid_module_goes_off(db_session: Session) -> None:
    assert check(db_session, status())["found"] == []  # 14 days of trial left
    assert check(db_session, status(stored=licence()))["found"] == []
    base_only = ("core_5651",)
    # A licence of 5651 Temel only: its end and its problems switch nothing off.
    ending = licence(modules=base_only, valid_until=date(2026, 10, 25))
    assert check(db_session, status(stored=ending))["found"] == []
    gone = licence(modules=base_only, valid_until=date(2026, 10, 19))
    assert status(stored=gone).state == "expired"
    assert check(db_session, status(stored=gone))["found"] == []
    assert check(db_session, status(stored=licence(modules=base_only), server_id=OTHER)) == {
        "found": [],
        "opened": [],
        "resolved": [],
    }
    assert db_session.scalars(select(Alert)).all() == []


def test_a_new_occurrence_replaces_the_active_alert(db_session: Session) -> None:
    check(db_session, locked("expired"))
    first = active(db_session)["R20-license:off"]
    assert first.severity == "medium"
    report = check(db_session, locked("invalid"))
    assert report["resolved"] == ["R20-license:off"]
    assert report["opened"] == ["R20-license:off"]
    (old,) = closed(db_session, "R20-license:off")
    assert (old.id, reason(old)) == (first.id, "license_changed")
    new = active(db_session)["R20-license:off"]
    assert (new.severity, new.details["case"]) == ("high", "invalid")


def test_a_person_closing_it_is_not_raised_again_for_the_same_occurrence(
    db_session: Session,
) -> None:
    """A site that runs 5651 Temel only stays unlicensed for good: closed once, it stays closed."""
    unlicensed = status(started=NOW - 30 * DAY)
    check(db_session, unlicensed)
    dismiss(db_session, active(db_session)["R20-license:off"], NOW)
    for minutes in (5, 10, 15):
        report = check(db_session, unlicensed, NOW + timedelta(minutes=minutes))
        assert report["found"] == ["R20-license:off"]
        assert report["opened"] == []
    assert active(db_session) == {}
    # A licence comes and goes: the state changed, so the next "off" is a new occurrence.
    check(db_session, status(stored=licence()), NOW + DAY)
    report = check(db_session, unlicensed, NOW + 2 * DAY)
    assert report["opened"] == ["R20-license:off"]
    assert len(db_session.scalars(select(Alert).where(Alert.rule == "R20-license")).all()) == 2


# -- limits ---------------------------------------------------------------------------------------


def test_limits_crossed_from_the_crossing_event_on(db_session: Session) -> None:
    stored = licence(users=3, log_sources=4)
    over = status(stored=stored, users=5, sources=6, unconfigured=2)
    assert check(db_session, over)["found"] == []  # the first state: no limit event yet
    report = check(db_session, over, NOW + timedelta(minutes=5))
    assert report["opened"] == ["R20-license-limit:users", "R20-license-limit:log_sources"]
    alerts = active(db_session)
    users, sources = alerts["R20-license-limit:users"], alerts["R20-license-limit:log_sources"]
    assert (users.rule, users.severity, users.title) == (
        "R20-license-limit",
        "medium",
        "Kullanıcı sınırı aşıldı: 5/3",
    )
    assert {k: users.details[k] for k in ("limit", "used", "max")} == {
        "limit": "users",
        "used": 5,
        "max": 3,
    }
    assert sources.details["unconfigured"] == 2
    assert describe("R20-license-limit", sources.details) == (
        "Log kaynağı sayısı lisans sınırını aşıyor (6/4; 2 yapılandırılmamış). Kayıtlar "
        "toplanmaya ve mühürlenmeye devam ediyor.",
        "The number of log sources exceeds the licence limit (6/4; 2 unconfigured). Logs are "
        "still collected and sealed.",
    )
    assert check(db_session, over, NOW + timedelta(minutes=10))["opened"] == []
    # Users back within the limit: that alert is resolved, the other stays.
    within = status(stored=stored, users=3, sources=6, unconfigured=2)
    report = check(db_session, within, NOW + timedelta(minutes=15))
    assert report["resolved"] == ["R20-license-limit:users"]
    assert reason(closed(db_session, "R20-license-limit:users")[0]) == "limit_ok"
    assert list(active(db_session)) == ["R20-license-limit:log_sources"]
    # Closed by a person: not again for this crossing, but for the next one.
    dismiss(db_session, active(db_session)["R20-license-limit:log_sources"], NOW)
    assert check(db_session, within, NOW + timedelta(minutes=20))["opened"] == []
    under = status(stored=stored, users=3, sources=4)
    check(db_session, under, NOW + timedelta(minutes=25))
    report = check(db_session, within, NOW + timedelta(minutes=30))
    assert report["opened"] == ["R20-license-limit:log_sources"]


def test_no_limit_alert_without_a_limit(db_session: Session) -> None:
    trial = status(users=40, sources=900)  # the trial has no limits
    for minutes in (0, 5):
        assert check(db_session, trial, NOW + timedelta(minutes=minutes))["found"] == []


# -- maintenance ----------------------------------------------------------------------------------


def test_maintenance_ending_and_ended(db_session: Session) -> None:
    far = licence(maintenance_until=date(2026, 11, 20))  # 31 days left at NOW
    assert check(db_session, status(stored=far))["found"] == []
    report = check(db_session, status(at=NOW + DAY, stored=far), NOW + DAY)
    assert report["opened"] == ["R20-license-maintenance"]
    alert = active(db_session)["R20-license-maintenance"]
    assert (alert.rule, alert.severity, alert.title) == (
        "R20-license-maintenance",
        "info",
        "Bakım süresi bitiyor: 20.11.2026 (30 gün kaldı)",
    )
    assert describe("R20-license-maintenance", alert.details) == (
        "Bakım süresi 30 gün sonra bitiyor (20.11.2026): yeni sürümler ve destek için yenileyin. "
        "Lisans geçerli kalır.",
        "Maintenance ends in 30 day(s) (20.11.2026): renew it for new versions and support. The "
        "licence stays valid.",
    )
    # Ended (this version came out before: still licensed): the same alert, now "ended".
    ended = NOW + 32 * DAY
    current = status(at=ended, stored=far)
    assert (current.state, current.maintenance_expired) == ("licensed", True)
    check(db_session, current, ended)
    alert = active(db_session)["R20-license-maintenance"]
    assert alert.title == "Bakım süresi doldu: 20.11.2026"
    assert describe("R20-license-maintenance", alert.details)[1].startswith(
        "Maintenance has ended (20.11.2026)"
    )
    # Renewed.
    report = check(db_session, status(at=ended, stored=licence()), ended)
    assert report["resolved"] == ["R20-license-maintenance"]
    assert reason(closed(db_session, "R20-license-maintenance")[0]) == "maintenance_renewed"


def test_maintenance_alert_only_for_a_valid_licence(db_session: Session) -> None:
    soon = licence(maintenance_until=date(2026, 11, 1))
    check(db_session, status(stored=soon))
    assert "R20-license-maintenance" in active(db_session)
    report = check(db_session, status(stored=soon, server_id=OTHER))
    assert "R20-license-maintenance" in report["resolved"]
    assert reason(closed(db_session, "R20-license-maintenance")[0]) == "license_changed"


# -- the job --------------------------------------------------------------------------------------


@pytest.fixture
def server_identity() -> Iterator[str]:
    clear_cache()
    with fixed_identity() as server_id:
        yield server_id
    clear_cache()


@pytest.mark.usefixtures("server_identity")
def test_license_check_raises_and_resolves_the_alerts(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    now = utc_now()
    db_session.add(
        LicenseInstallation(id=1, installation_id=uuid4(), trial_started_at=now - 12 * DAY)
    )
    db_session.commit()
    worker = Worker(engine=db_engine, layout=layout, jobs=[], clock=lambda: now)
    details = worker_licensing.job_license_check(worker.context(now))
    assert details["status"] == "trial"
    assert details["alerts"]["opened"] == ["R20-license:trial-ending"]
    later = now + 3 * DAY
    details = worker_licensing.job_license_check(worker.context(later))
    assert details["status"] == "trial_expired"
    assert details["alerts"] == {
        "found": ["R20-license:off"],
        "opened": ["R20-license:off"],
        "resolved": ["R20-license:trial-ending"],
    }
    events = db_session.scalars(select(AlertEvent).order_by(AlertEvent.id)).all()
    assert [(e.to_state, e.text, e.actor) for e in events] == [
        ("resolved", "license_changed", "system")
    ]


@pytest.mark.usefixtures("server_identity")
def test_the_licence_check_never_fails_because_of_its_alerts(
    db_engine: Engine,
    db_session: Session,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("a bug in the licence alerts")

    monkeypatch.setattr(worker_licensing, "check_license", broken)
    jobs = [job for job in default_jobs(60) if job.name in ("license.check", "leases.incremental")]
    worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=utc_now)
    assert dict(worker.tick()) == {"license.check": "ok", "leases.incremental": "ok"}
    run = db_session.scalars(select(JobRun).where(JobRun.job == "license.check")).one()
    assert run.details["alerts"] == {"error": "RuntimeError: a bug in the licence alerts"}
    assert run.details["recorded"] == ["state"]  # the licence events are kept
    assert (layout.state / "license-status.json").is_file()
    assert "licence alerts failed" in caplog.text


# -- what the e-mail and the web may say ----------------------------------------------------------


def test_the_sentences_of_every_case() -> None:
    trial = {
        "case": "trial_ending",
        "days_left": 3,
        "until": "2026-10-23T12:00:00+03:00",
        "modules": PAID,
    }
    assert describe("R20-license", trial) == (
        "Deneme süresinin bitmesine 3 gün kaldı (23.10.2026 12:00:00); ardından ücretli "
        f"modüller kapanır: {NAMES_TR}. 5651 işlevleri çalışmaya devam eder.",
        f"3 day(s) left of the trial (23.10.2026 12:00:00); then the paid modules go off: "
        f"{NAMES_EN}. The 5651 functions keep working.",
    )
    last = {"case": "expiring", "days_left": 0, "until": "2026-11-04", "modules": ["bulk_import"]}
    assert describe("R20-license", last) == (
        "Lisans 04.11.2026 tarihine kadar geçerli (bugün son gün); ardından ücretli modüller "
        "kapanır: Toplu içe aktarma (CSV). 5651 işlevleri çalışmaya devam eder.",
        "The licence is valid until 04.11.2026 (today is the last day); then the paid modules go "
        "off: Bulk import (CSV). The 5651 functions keep working.",
    )
    uncovered = {"case": "version_not_covered", "until": "2026-09-01", "modules": PAID}
    assert describe("R20-license", uncovered) == (
        "Bu sürüm bakım süresinden (01.09.2026 tarihine kadar) sonra yayımlandı: ücretli "
        f"modüller kapalı ({NAMES_TR}). 5651 işlevleri çalışmaya devam ediyor.",
        "This version was released after the maintenance period (until 01.09.2026): the paid "
        f"modules are off ({NAMES_EN}). The 5651 functions keep working.",
    )
    users = {"limit": "users", "used": 5, "max": 3}
    assert describe("R20-license-limit", users) == (
        "Kullanıcı sayısı lisans sınırını aşıyor (5/3): yeni kullanıcı eklenemez. 5651 "
        "işlevleri etkilenmez.",
        "The number of users exceeds the licence limit (5/3): no new user can be added. The "
        "5651 functions are not affected.",
    )
    today = {"days_left": 0, "until": "2026-11-20"}
    assert describe("R20-license-maintenance", today)[0].startswith("Bakım süresi bugün bitiyor")
    assert rule_text("R20-license").name_en == "Paid modules ending or off (licence)"


@pytest.mark.parametrize(
    ("rule", "details"),
    [
        ("R20-license", {"case": "expired", "modules": PAID}),  # the date is missing
        ("R20-license", {"case": "wrong_server", "modules": "usage_analytics"}),
        ("R20-license", {"case": "wrong_server", "modules": ["x", PERSON, "203.0.113.10"]}),
        ("R20-license", {"case": PERSON, "modules": PAID}),
        (
            "R20-license",
            {"case": "trial_ending", "days_left": "3", "until": "2026-10-23", "modules": PAID},
        ),
        ("R20-license-limit", {"limit": "users", "used": 5}),
        ("R20-license-limit", {"limit": PERSON, "used": 5, "max": 3}),
        ("R20-license-maintenance", {"days_left": 3, "until": RAW}),
    ],
)
def test_details_out_of_shape_give_no_sentence(rule: str, details: dict[str, Any]) -> None:
    assert describe(rule, details) == GENERIC


def test_an_e_mail_holds_only_whitelisted_facts() -> None:
    details = {
        "case": "wrong_server",
        "state": "wrong_server",
        "problem": "license_other_server",
        # None of these is a fact an e-mail may carry.
        "modules": ["usage_analytics", PERSON, "203.0.113.10"],
        "licensee": PERSON,
        "server_id": OTHER,
        "until": None,
        "source": RAW,
        "title_en": RAW,
    }
    items = [MailItem(1, "new", "R20-license", "high", "open", NOW, None, details)]
    message = build_message(
        items,
        sender="arcilogger@example.com",
        recipient="bilgi-islem@example.com",
        web_url="https://arcilogger.internal",
        now=NOW,
    )
    subject = str(message["Subject"])
    assert subject == "[ARCILOGGER] Yüksek: Ücretli modüller kapanıyor ya da kapalı (lisans)"
    plain, rich = (message.get_body(preferencelist=(kind,)) for kind in ("plain", "html"))
    assert plain is not None
    assert rich is not None
    text, page = str(plain.get_content()), str(rich.get_content())
    assert "Lisans bu sunucu için değil: ücretli modüller kapalı (Kullanım analizi)." in text
    assert "The licence is not for this server: the paid modules are off (Usage analytics)." in text
    for secret in (PERSON, OTHER, "203.0.113.10", "172.20.42.57", "license_other_server"):
        assert secret not in text
        assert secret not in page
        assert secret not in subject


def test_without_the_licence_package_the_e_mail_says_nothing_of_the_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "arcilogger_core.license.modules", None)
    details = {"case": "trial_expired", "modules": PAID}
    assert describe("R20-license", details) == GENERIC
