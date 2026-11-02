"""``alerts.system``: the prepared legal-validity checks of Wave 5 contract 2.3.1 — re-stamping
(``R20-renewal``), the time-stamp certificate (``R20-cert``) and the revocation lists
(``R20-crl``) — run as ``alerts.system`` runs its checks, with :data:`~arcilogger_worker.alerts.
system.LEGAL_CHECKS` in place of the registered ones (W5-E switches them on). Their e-mails name
no certificate and no person, and until W5-E no Python string constant spells a whole code (D14).

The state files are written as their writers write them (``test_alerts_legal_state.py``)."""

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert
from arcilogger_worker.alerts import system
from arcilogger_worker.alerts.mail import MailItem, render_html, render_text, subject
from arcilogger_worker.alerts.system import (
    CERT_KEY,
    CRL_INDEX_SCHEMA,
    CRL_KEY,
    LEGAL_CHECKS,
    RENEWAL_KEY,
    crl_index_path,
    run_system_checks,
    stamp_renewal_path,
)
from arcilogger_worker.vaulttesting import at_site

ROOT = Path(__file__).resolve().parents[2]
NOW = at_site(date(2026, 11, 2), 9, 0)
ROOT_SHA = "a1" * 32
CERT_NAME = "CN=Örnek Test TSA 1,O=Örnek A.Ş.,C=TR"
REVOKED = (
    f"A time-stamp certificate is on a revocation list: {CERT_NAME}. Contact the provider; "
    "legal review is recommended."
)
URL = "https://arcilogger.example.com"
#: The alert codes whose whole-string constants only W5-E adds (D14).
WAVE5_CODES = frozenset({"R20-renewal", "R20-cert", "R20-crl"})
#: The registered checks (the fixture below replaces them for each test).
REGISTERED = system.CHECKS


@pytest.fixture(autouse=True)
def only_the_legal_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The three prepared checks, each in its own transaction as alerts.system runs them."""
    assert [name for name, _ in LEGAL_CHECKS] == ["stamp_renewal", "stamp_cert", "stamp_crl"]
    monkeypatch.setattr(system, "CHECKS", LEGAL_CHECKS)


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
    def run(now: datetime = NOW, using: Config | None = None) -> dict[str, Alert]:
        db_session.commit()
        run_system_checks(sessions, layout, using or config, now=now)
        return active(db_session)

    return run


def active(session: Session) -> dict[str, Alert]:
    session.expire_all()
    rows = session.scalars(select(Alert).where(Alert.state.in_(("open", "acknowledged"))))
    return {row.dedup_key or "": row for row in rows}


def resolved(session: Session, key: str) -> Alert:
    session.expire_all()
    return session.scalars(
        select(Alert).where(Alert.dedup_key == key, Alert.state == "resolved")
    ).one()


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def renewals(layout: Layout, *statuses: tuple[str, datetime]) -> None:
    """``state/renewal.json`` with one day token per ``(status, cert_not_after)``."""
    tokens = []
    for number, (status, not_after) in enumerate(statuses, start=1):
        day = f"2026-10-{number:02d}"
        tokens.append(
            {
                "path": f"2026/10/{number:02d}/manifest-{day}.json.zd",
                "kind": "day",
                "cert_not_after": not_after.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "status": status,
                "covered_by": "renewals/2026-10-20-1.json" if status == "covered" else None,
            }
        )
    write(stamp_renewal_path(layout), {"tokens": tokens, "totals": {"wrong": 99}})


def revoked_day(layout: Layout, day: date = date(2026, 10, 30)) -> None:
    """A nightly verification that found the day's certificate revoked."""
    write(
        layout.state / "verify" / "last-day.json",
        {
            "at": isoformat_site(NOW - timedelta(hours=6), "seconds"),
            "deep": False,
            "checks": [{"day": day.isoformat(), "ok": False, "errors": [REVOKED], "warnings": []}],
        },
    )


def crl_index(
    layout: Layout,
    issued_days_ago: int,
    *,
    fetched_days_ago: int = 0,
    failures: tuple[dict[str, Any], ...] = (),
) -> None:
    this_update = NOW - timedelta(days=issued_days_ago)
    entry = {
        "file": f"{ROOT_SHA[:12]}-{this_update:%Y%m%dT%H%M%S}Z-89abcdef.crl",
        "issuer": "CN=Örnek Test TSA Kök,O=Örnek A.Ş.,C=TR",
        "issuer_sha256": ROOT_SHA,
        "this_update": this_update.astimezone(UTC).isoformat(),
        "next_update": None,
        "sha256": "c3" * 32,
        "url": "http://192.0.2.10/crl/root.crl",
        "fetched_at": isoformat_site(NOW - timedelta(days=fetched_days_ago), "seconds"),
    }
    write(
        crl_index_path(layout),
        {"schema": CRL_INDEX_SCHEMA, "lists": [entry], "failures": list(failures)},
    )


FAILURE: dict[str, Any] = {
    "url": "http://192.0.2.10/crl/root.crl",
    "at": isoformat_site(NOW - timedelta(hours=1), "seconds"),
    "error": "connection refused",
}


# -- R20-renewal ----------------------------------------------------------------------------------


def test_stamp_renewal_raised_for_due_and_uncovered_and_cleared(
    check: Any, layout: Layout, db_session: Session
) -> None:
    later = NOW + timedelta(days=100)
    renewals(layout, ("not_due", NOW + timedelta(days=400)), ("covered", later), ("late", later))
    assert RENEWAL_KEY not in check()  # negative: nothing due, a late one is the day page's
    renewals(
        layout,
        ("due", later),
        ("due", later),
        ("uncovered", NOW - timedelta(days=3)),
        ("covered", later),
    )
    alert = check()[RENEWAL_KEY]
    assert (alert.rule, alert.severity) == ("R20-renewal", "high")
    # Counted from the tokens, never from the report's totals.
    assert {key: alert.details[key] for key in ("due", "uncovered")} == {"due": 2, "uncovered": 1}
    assert alert.title == (
        "Yeniden damgalama gerekiyor: 2 damganın zamanı geldi, 1 damga yeniden damgalanmadan "
        "sona erdi"
    )
    assert alert.details["title_en"] == (
        "Re-stamping needed: 2 stamp(s) due, 1 expired without re-stamping"
    )
    renewals(layout, ("uncovered", NOW - timedelta(days=3)))
    assert check()[RENEWAL_KEY].details["due"] == 0  # still open, brought up to date
    renewals(layout, ("covered", later))
    assert RENEWAL_KEY not in check()
    reason = resolved(db_session, RENEWAL_KEY).details["auto_resolved"]["reason"]
    assert reason == "renewal_ok"


def test_stamp_renewal_unknown_report_decides_nothing(check: Any, layout: Layout) -> None:
    assert RENEWAL_KEY not in check()  # no report yet: the Renew task has not run
    renewals(layout, ("due", NOW + timedelta(days=100)))
    assert RENEWAL_KEY in check()
    stamp_renewal_path(layout).write_text('{"tokens": [', encoding="utf-8")
    assert RENEWAL_KEY in check()  # unreadable: neither raised again nor resolved
    write(stamp_renewal_path(layout), {"tokens": "none"})
    assert RENEWAL_KEY in check()


# -- R20-cert -------------------------------------------------------------------------------------


def test_stamp_cert_critical_when_revoked(check: Any, layout: Layout) -> None:
    renewals(layout, ("due", NOW + timedelta(days=10)))
    revoked_day(layout)
    alert = check()[CERT_KEY]
    assert (alert.rule, alert.severity) == ("R20-cert", "critical")
    assert alert.details == {
        "revoked": 1,
        "expiring": 1,
        "title_en": "Time-stamp certificate on a revocation list: 1 certificate(s)",
    }
    assert alert.title == "Zaman damgası sertifikası iptal listesinde: 1 sertifika"
    assert "Örnek" not in alert.title


def test_stamp_cert_high_when_expiring_within_30_days_uncovered(check: Any, layout: Layout) -> None:
    renewals(
        layout,
        ("due", NOW + timedelta(days=30, seconds=1)),  # just outside
        ("covered", NOW + timedelta(days=5)),  # re-stamped already
        ("late", NOW + timedelta(days=5)),
        ("not_due", NOW + timedelta(days=5)),
    )
    assert CERT_KEY not in check()  # negative
    renewals(layout, ("due", NOW + timedelta(days=30)), ("due", NOW + timedelta(days=12)))
    alert = check()[CERT_KEY]
    assert (alert.rule, alert.severity) == ("R20-cert", "high")
    assert (alert.details["revoked"], alert.details["expiring"]) == (0, 2)
    assert alert.title == (
        "Zaman damgası sertifikası 30 gün içinde sona eriyor: 2 damga yeniden damgalanmadı"
    )
    assert alert.details["title_en"] == (
        "Time-stamp certificate expires within 30 days: 2 stamp(s) not re-stamped"
    )
    revoked_day(layout)
    assert check()[CERT_KEY].severity == "critical"  # a revocation raises it


def test_stamp_cert_cleared(check: Any, layout: Layout, db_session: Session) -> None:
    renewals(layout, ("due", NOW + timedelta(days=12)))
    assert CERT_KEY in check()
    stamp_renewal_path(layout).write_text("{", encoding="utf-8")
    assert CERT_KEY in check()  # an unreadable report tells nothing
    renewals(layout, ("covered", NOW + timedelta(days=12)))
    assert CERT_KEY not in check()
    reason = resolved(db_session, CERT_KEY).details["auto_resolved"]["reason"]
    assert reason == "certificate_ok"


# -- R20-crl --------------------------------------------------------------------------------------


def test_stamp_crl_raised_when_stale_and_cleared_after_a_fresh_list(
    check: Any, layout: Layout, config: Config, db_session: Session
) -> None:
    assert CRL_KEY not in check()  # no index before the first fetch: no issuer in use
    crl_index(layout, 6)
    assert CRL_KEY not in check()
    crl_index(layout, 7)  # exactly seven days old is not "newer than seven days"
    alert = check()[CRL_KEY]
    assert (alert.rule, alert.severity) == ("R20-crl", "medium")
    assert alert.title == "7 günden yeni doğrulanmış bir iptal listesi (SİL) yok"
    assert alert.details == {
        "stale_issuers": 1,
        "failures": 0,
        "newest_fetched_at": isoformat_site(NOW, "seconds"),
        "max_age_days": 7,
        "title_en": "No verified revocation list newer than 7 days",
    }
    crl_index_path(layout).write_text("{", encoding="utf-8")
    assert CRL_KEY in check()  # an unreadable index resolves nothing
    crl_index(layout, 1)
    assert CRL_KEY not in check()
    assert resolved(db_session, CRL_KEY).details["auto_resolved"]["reason"] == "crl_fresh"

    # tsa.crl_max_age_days (Wave 5 contract 2.5) sets the age.
    strict = config.model_copy(
        update={"tsa": config.tsa.model_copy(update={"crl_max_age_days": 1})}
    )
    assert CRL_KEY in check(using=strict)
    assert active(db_session)[CRL_KEY].details["max_age_days"] == 1


def test_stamp_crl_judged_from_the_index_not_an_exit_code(check: Any, layout: Layout) -> None:
    """C12: the fetch is judged from ``state/crl/index.json``. Failed fetches and an old
    ``fetched_at`` alone raise nothing while the stored list is recent; once the list is old the
    alert counts them from the index."""
    crl_index(layout, 4, fetched_days_ago=4, failures=(FAILURE, FAILURE))
    assert CRL_KEY not in check()
    crl_index(layout, 9, fetched_days_ago=8, failures=(FAILURE,))
    alert = check()[CRL_KEY]
    assert alert.details["failures"] == 1
    assert alert.details["newest_fetched_at"] == isoformat_site(NOW - timedelta(days=8), "seconds")


# -- the e-mails ----------------------------------------------------------------------------------


def test_legal_alert_mails_carry_no_names_and_no_log_lines(check: Any, layout: Layout) -> None:
    renewals(layout, ("due", NOW + timedelta(days=10)), ("uncovered", NOW - timedelta(days=1)))
    revoked_day(layout)
    crl_index(layout, 9, failures=(FAILURE,))
    alerts = check()
    items = [
        MailItem(a.id, "new", a.rule, a.severity, a.state, a.time, None, a.details)
        for key, a in sorted(alerts.items())
        if key in (RENEWAL_KEY, CERT_KEY, CRL_KEY)
    ]
    assert [item.rule for item in items] == ["R20-cert", "R20-crl", "R20-renewal"]
    assert subject(items).startswith("[ARCILOGGER] ")
    for mail in (render_text(items, URL), render_html(items, URL)):
        for rule in ("R20-cert", "R20-crl", "R20-renewal"):
            assert rule in mail
        for hidden in (
            "Örnek",
            "Test TSA",
            "CN=",
            "revocation list:",  # the day check's error line
            "192.0.2.",
            "connection refused",
            "manifest-",
            "renewals/",
            "172.20.",
            "00:00:5E",
        ):
            assert hidden not in mail, hidden
    for alert in alerts.values():
        shown = alert.title + json.dumps(alert.details, ensure_ascii=False)
        for hidden in ("Örnek", "192.0.2.", "manifest-"):
            assert hidden not in shown, hidden


# -- D14 ------------------------------------------------------------------------------------------


def _code_constants() -> list[str]:
    """Where a source file spells a whole Wave 5 alert code as a string constant, in the packages
    ``tests/test_docs_consistency.py::_alert_codes`` reads (every code it finds there needs a
    first-steps guide row). A literal rule given to ``raise_alert``/``_alert`` and the text
    parts of an f-string are such constants too."""
    found = []
    for package in ("core", "collector", "ingest", "worker", "api", "db"):
        for path in sorted((ROOT / package / "src").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and node.value in WAVE5_CODES:
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value}")
    return found


def test_no_whole_string_constant_for_the_wave5_alert_codes() -> None:
    """D14: only the switch-on package W5-E adds the whole-string constants ``R20-renewal``,
    ``R20-cert`` and ``R20-crl``, with their texts and first-steps guide rows. Until then the
    checks write each code only inside its dedup key.

    W5-E: delete or invert this test in the pull request that adds the constants."""
    assert _code_constants() == []
    assert {system._rule_of(key) for key in (RENEWAL_KEY, CERT_KEY, CRL_KEY)} == WAVE5_CODES
    # Prepared, not switched on (W5-E appends them).
    assert {check for _, check in REGISTERED} & {check for _, check in LEGAL_CHECKS} == set()
