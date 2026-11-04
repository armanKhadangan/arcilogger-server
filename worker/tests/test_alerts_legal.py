"""``alerts.system``: the legal-validity checks of Wave 5 contract 2.3.1 — re-stamping
(``R20-renewal``), the time-stamp certificate (``R20-cert``) and the revocation lists
(``R20-crl``) — run as ``alerts.system`` runs its checks, with :data:`~arcilogger_worker.alerts.
system.LEGAL_CHECKS` in place of the registered ones (one test runs every registered check).
Their e-mails name no certificate and no person, and only the alerts' own modules spell a whole
code (D14: W5-E switched them on).

The state files are written as their writers write them (``test_alerts_legal_state.py``)."""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, load_pem_private_key
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.crl import CrlOptions, parse_crl
from arcilogger_core.crl import fetch as crl_fetch
from arcilogger_core.layout import Layout
from arcilogger_core.renewal import LastRun, write_state
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import isoformat_site
from arcilogger_core.tsa.mock import MockTsaProvider
from arcilogger_core.tsa.verify import TEST_ORGANIZATION, fingerprint
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert
from arcilogger_worker.alerts import system
from arcilogger_worker.alerts.mail import MailItem, render_html, render_text, subject
from arcilogger_worker.alerts.system import (
    CERT_EXPIRY_WARNING,
    CERT_KEY,
    CERT_RULE,
    CRL_INDEX_SCHEMA,
    CRL_KEY,
    CRL_RULE,
    LEGAL_CHECKS,
    RENEW_FAILURES_ALERT,
    RENEWAL_KEY,
    RENEWAL_RULE,
    RenewRuns,
    crl_index_path,
    read_renew_runs,
    run_system_checks,
    stamp_renewal_path,
)
from arcilogger_worker.alerts.texts import (
    DESCRIBERS,
    RULES,
    UNKNOWN_RUNBOOK,
    describe,
    rule_text,
    runbook,
)
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

ROOT = Path(__file__).resolve().parents[2]
NOW = at_site(date(2026, 11, 2), 9, 0)
CERT_NAME = "CN=Örnek Test TSA 1,O=Örnek A.Ş.,C=TR"
REVOKED = (
    f"A time-stamp certificate is on a revocation list: {CERT_NAME}. Contact the provider; "
    "legal review is recommended."
)
URL = "https://arcilogger.example.com"
#: The alert codes whose whole-string constants only the switch-on package W5-E adds (D14).
WAVE5_CODES = frozenset({"R20-renewal", "R20-cert", "R20-crl"})
#: The registered checks (the fixture below replaces them for each test).
REGISTERED = system.CHECKS
#: The dedup keys of the three alerts.
KEYS = (RENEWAL_KEY, CERT_KEY, CRL_KEY)
#: A made-up CA a real install's stamps chain to: not ARCILOGGER's test organization, and a
#: policy outside the test arc 2.999.
CA_ORGANIZATION = "Örnek Simulated CA"
CA_POLICY = "1.3.6.1.4.1.55555.1.1"


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
    """The mock provider (its test root is pinned once the mock TSA exists) and the pinned
    roots of :func:`roots_dir` (empty until a test puts one there)."""
    return Config(
        data_dir=layout.root, secrets_dir=layout.root, tsa={"roots_dir": roots_dir(layout)}
    )


def roots_dir(layout: Layout) -> Path:
    """``tsa.roots_dir`` of :func:`config`, outside the data directory."""
    return layout.root.parent / "kokler"


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


@dataclass(frozen=True)
class Stamps:
    """A sealed vault and who stamped it: the root its days' chain reaches (whose key signs the
    root's revocation lists) and the TSA certificate."""

    vault: SealedVault
    root: x509.Certificate
    key: ec.EllipticCurvePrivateKey
    tsa: x509.Certificate

    @property
    def layout(self) -> Layout:
        return self.vault.layout


def _name(organization: str, common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


@pytest.fixture
def sealed(layout: Layout) -> Iterator[Stamps]:
    """30 October sealed by the mock TSA (its root is pinned while ``tsa.provider`` is
    ``mock``), as on a mock or trial install: ARCILOGGER's test root publishes no list."""
    vault = make_vault(layout.root, tsa_now=at_site(date(2026, 9, 29)))
    vault.seal_days([date(2026, 10, 30)])
    key = load_pem_private_key((vault.tsa.state_dir / "root.key").read_bytes(), None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    assert TEST_ORGANIZATION in vault.tsa.root_certificate.subject.rfc4514_string()
    yield Stamps(vault, vault.tsa.root_certificate, key, vault.tsa.tsa_certificate)
    vault.unlock_all()


@pytest.fixture
def ca_sealed(layout: Layout) -> Iterator[Stamps]:
    """30 October sealed as on a real install: a made-up CA's TSA certificate, its root pinned
    in ``tsa.roots_dir``; the stamp in use, whose root's lists must stay fresh."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = _name(CA_ORGANIZATION, "Örnek Simulated Root")
    root = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2070, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    tsa_key = ec.generate_private_key(ec.SECP256R1())
    tsa = (
        x509.CertificateBuilder()
        .subject_name(_name(CA_ORGANIZATION, "Örnek Simulated TSA"))
        .issuer_name(name)
        .public_key(tsa_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2031, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(key, hashes.SHA256())
    )
    roots_dir(layout).mkdir(parents=True)
    (roots_dir(layout) / "root.pem").write_bytes(root.public_bytes(Encoding.PEM))
    vault = make_vault(layout.root, tsa_now=at_site(date(2026, 9, 29)))
    vault.tsa.signer = (tsa, tsa_key)
    vault.tsa.policy = CA_POLICY
    day = date(2026, 10, 30)
    vault.write_day(day)
    now = at_site(day + timedelta(days=1))
    vault.tsa_clock.now = now + timedelta(seconds=5)
    sealer = Sealer(
        vault.config,
        provider=MockTsaProvider(vault.tsa),
        roots=[root],
        clock=lambda: now,
        clock_status=lambda: "test clock status",
    )
    assert sealer.seal_day(day).status == "sealed"
    yield Stamps(vault, root, key, tsa)
    vault.unlock_all()


def root_list(
    stamps: Stamps, this_update: datetime, revoked_at: datetime | None = None, valid_days: int = 7
) -> bytes:
    """A list of the stamps' root issued at ``this_update`` and valid for ``valid_days``, with
    the TSA certificate revoked at ``revoked_at`` (none: no entry)."""
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(stamps.root.subject)
        .last_update(this_update)
        .next_update(this_update + timedelta(days=valid_days))
    )
    if revoked_at is not None:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(stamps.tsa.serial_number)
            .revocation_date(revoked_at)
            .build()
        )
    return builder.sign(stamps.key, hashes.SHA256()).public_bytes(Encoding.DER)


def crl_lists(
    stamps: Stamps,
    issued_days_ago: int | None,
    *,
    fetched_days_ago: int = 0,
    failures: tuple[dict[str, Any], ...] = (),
    revoked_at: datetime | None = None,
    valid_days: int = 7,
) -> str | None:
    """``state/crl`` as ``crl fetch`` leaves it (Wave 5 contract 2.2): the root's list issued
    ``issued_days_ago`` days before ``NOW`` (none: no list), valid for ``valid_days`` and last
    fetched ``fetched_days_ago`` days before ``NOW``, and ``failures``; its stored name."""
    layout = stamps.layout
    layout.crl_dir.mkdir(parents=True, exist_ok=True)
    entries, name = [], None
    if issued_days_ago is not None:
        der = root_list(stamps, NOW - timedelta(days=issued_days_ago), revoked_at, valid_days)
        crl = replace(parse_crl(der), issuer_sha256=fingerprint(stamps.root))
        name = crl.file_name
        (layout.crl_dir / name).write_bytes(der)
        entries.append(
            {
                "file": name,
                "issuer": crl.issuer,
                "issuer_sha256": crl.issuer_sha256,
                "this_update": crl.this_update.isoformat(),
                "next_update": crl.next_update.isoformat() if crl.next_update else None,
                "sha256": crl.sha256,
                "url": "http://192.0.2.10/crl/root.crl",
                "fetched_at": isoformat_site(NOW - timedelta(days=fetched_days_ago), "seconds"),
            }
        )
    write(
        crl_index_path(layout),
        {"schema": CRL_INDEX_SCHEMA, "lists": entries, "failures": list(failures)},
    )
    return name


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


#: The exit code of each ``last_run.outcome`` (W5-A-9).
EXIT_CODES: dict[str, int] = {"renewed": 0, "not_needed": 0, "refused": 2, "failed": 3}


def renew_runs(layout: Layout, *outcomes: str) -> None:
    """``state/renewal.json`` after the ``Renew`` task's runs ``outcomes``, written by W5-A's
    writer as the task writes it when its statuses cannot be computed (the token list, if any,
    is kept). ``dry`` is a dry run: it keeps ``last_run`` and ``failures_in_a_row``."""
    for number, outcome in enumerate(outcomes):
        at = NOW - timedelta(hours=len(outcomes) - number)
        last_run = None
        if outcome != "dry":
            error = {"refused": "roots", "failed": "tsa_unavailable"}.get(outcome)
            last_run = LastRun(at, outcome, EXIT_CODES[outcome], error=error)  # type: ignore[arg-type]
        write_state(layout, None, at=at, renew_before=180, last_run=last_run)


def test_stamp_renewal_raised_after_two_failed_runs(check: Any, layout: Layout) -> None:
    """Contract 2.3.1 and A4: "the Renew task failed twice in a row" is ``failures_in_a_row``
    of ``state/renewal.json`` at least 2, read from the file, never from an exit code."""
    assert RENEW_FAILURES_ALERT == 2
    renewals(layout, ("not_due", NOW + timedelta(days=400)))
    renew_runs(layout, "renewed", "failed")
    assert json.loads(stamp_renewal_path(layout).read_bytes())["failures_in_a_row"] == 1
    assert RENEWAL_KEY not in check()  # negative: one failure
    renew_runs(layout, "failed")
    alert = check()[RENEWAL_KEY]
    assert (alert.rule, alert.severity) == ("R20-renewal", "high")
    assert alert.details == {
        "due": 0,
        "uncovered": 0,
        "failures_in_a_row": 2,
        "title_en": "The re-stamping task failed 2 times in a row",
    }
    assert alert.title == "Yeniden damgalama görevi art arda 2 kez başarısız"
    assert describe(RENEWAL_RULE, alert.details) == (
        "Renew görevi art arda 2 kez başarısız oldu.",
        "The Renew task failed 2 times in a row.",
    )
    renew_runs(layout, "failed")
    assert check()[RENEWAL_KEY].details["failures_in_a_row"] == 3  # brought up to date


def test_stamp_renewal_refused_runs_count(check: Any, layout: Layout) -> None:
    """A refused run (exit 2: the configuration, for example no pinned root) counts like a
    failed one (A4)."""
    renewals(layout, ("not_due", NOW + timedelta(days=400)))
    renew_runs(layout, "refused")
    assert RENEWAL_KEY not in check()
    renew_runs(layout, "failed")
    assert check()[RENEWAL_KEY].details["failures_in_a_row"] == 2
    renew_runs(layout, "not_needed", "refused", "refused")
    assert check()[RENEWAL_KEY].details["failures_in_a_row"] == 2


def test_stamp_renewal_failures_without_tokens_raise(check: Any, layout: Layout) -> None:
    """A refused run without pinned roots computes no statuses and writes no token list
    (W5-A-2): the tokens are unknown, the failures still raise the alert."""
    renew_runs(layout, "refused")
    assert "tokens" not in json.loads(stamp_renewal_path(layout).read_bytes())
    assert RENEWAL_KEY not in check()
    renew_runs(layout, "refused")
    alert = check()[RENEWAL_KEY]
    assert alert.details == {
        "due": None,
        "uncovered": None,
        "failures_in_a_row": 2,
        "title_en": "The re-stamping task failed 2 times in a row",
    }
    assert alert.title == "Yeniden damgalama görevi art arda 2 kez başarısız"
    assert describe(RENEWAL_RULE, alert.details)[1] == "The Renew task failed 2 times in a row."


def test_stamp_renewal_title_follows_what_raised_it(check: Any, layout: Layout) -> None:
    """#151 (W5-E-7): raised by the Renew task's failures alone, the title says the task failed,
    with the count in a row, not that re-stamping is needed; a stamp due or expired without
    re-stamping gives "Re-stamping needed" with the failures added. The open alert's title
    follows each pass."""
    later = NOW + timedelta(days=100)
    renewals(layout, ("not_due", NOW + timedelta(days=400)))
    renew_runs(layout, "failed", "failed", "refused")
    alert = check()[RENEWAL_KEY]
    titles = (alert.title, alert.details["title_en"])
    assert titles == (
        "Yeniden damgalama görevi art arda 3 kez başarısız",
        "The re-stamping task failed 3 times in a row",
    )
    for word in ("gerekiyor", "needed"):  # negative: not the title of a stamp to re-stamp
        assert all(word not in title for title in titles), word
    renewals(layout, ("due", later))  # a token due: re-stamping is needed
    renew_runs(layout, "failed", "failed", "failed")
    alert = check()[RENEWAL_KEY]
    assert (alert.title, alert.details["title_en"]) == (
        "Yeniden damgalama gerekiyor: 1 damganın zamanı geldi, 0 damga yeniden damgalanmadan "
        "sona erdi; Renew görevi art arda 3 kez başarısız oldu",
        "Re-stamping needed: 1 stamp(s) due, 0 expired without re-stamping; the Renew task "
        "failed 3 times in a row",
    )
    renewals(layout, ("uncovered", NOW - timedelta(days=3)))  # an expired one, no failure
    assert check()[RENEWAL_KEY].title == (
        "Yeniden damgalama gerekiyor: 0 damganın zamanı geldi, 1 damga yeniden damgalanmadan "
        "sona erdi"
    )
    renewals(layout, ("covered", later))  # covered: then the failures alone hold it open
    renew_runs(layout, "refused", "failed", "failed")
    alert = check()[RENEWAL_KEY]
    assert (alert.title, alert.details["title_en"]) == titles
    for title in titles:  # counts only: no name, no address, no log line
        for hidden in ("Örnek", "Mock", "CN=", "192.0.2.", "tsa_unavailable", "roots"):
            assert hidden not in title, hidden


def test_stamp_renewal_cleared_after_a_successful_run(
    check: Any, layout: Layout, db_session: Session
) -> None:
    later = NOW + timedelta(days=100)
    renewals(layout, ("due", later))
    renew_runs(layout, "failed", "failed")
    alert = check()[RENEWAL_KEY]
    assert (alert.details["due"], alert.details["failures_in_a_row"]) == (1, 2)
    assert alert.title == (
        "Yeniden damgalama gerekiyor: 1 damganın zamanı geldi, 0 damga yeniden damgalanmadan "
        "sona erdi; Renew görevi art arda 2 kez başarısız oldu"
    )
    assert alert.details["title_en"] == (
        "Re-stamping needed: 1 stamp(s) due, 0 expired without re-stamping; the Renew task "
        "failed 2 times in a row"
    )
    assert describe(RENEWAL_RULE, alert.details) == (
        "1 damganın yeniden damgalama zamanı geldi, 0 damganın sertifikası yeniden "
        "damgalanmadan sona erdi. Renew görevi art arda 2 kez başarısız oldu.",
        "1 stamp(s) due for re-stamping, 0 expired without it. The Renew task failed 2 times "
        "in a row.",
    )
    renewals(layout, ("covered", later))  # a new report, then two more failed runs
    renew_runs(layout, "failed", "failed")
    assert check()[RENEWAL_KEY].details["due"] == 0  # nothing due, the failures hold it open
    renew_runs(layout, "dry")  # a dry run keeps them
    assert RENEWAL_KEY in check()
    renew_runs(layout, "renewed")
    assert RENEWAL_KEY not in check()
    reason = resolved(db_session, RENEWAL_KEY).details["auto_resolved"]["reason"]
    assert reason == "renewal_ok"


def test_stamp_renewal_unreadable_runs_decide_nothing(check: Any, layout: Layout) -> None:
    renewals(layout, ("not_due", NOW + timedelta(days=400)))
    renew_runs(layout, "failed", "failed")
    assert RENEWAL_KEY in check()
    path = stamp_renewal_path(layout)
    state = json.loads(path.read_bytes())
    for damaged in ("two", True, -1, 1.5, None, [2]):
        write(path, {**state, "failures_in_a_row": damaged})
        assert RENEWAL_KEY in check(), damaged  # nothing due, failures unknown: kept
    path.write_text('{"tokens": [', encoding="utf-8")
    assert RENEWAL_KEY in check()
    write(path, {**state, "tokens": "none"})  # failures known, tokens unknown: still raised
    assert check()[RENEWAL_KEY].details["due"] is None
    # A file without the count records no failure, as the writer reads it.
    write(path, {key: value for key, value in state.items() if key != "failures_in_a_row"})
    assert RENEWAL_KEY not in check()


def test_read_renew_runs_from_the_writers_file(layout: Layout) -> None:
    """The reader of the Sistem page's Renew row and of R20-renewal, against W5-A's writer."""
    assert read_renew_runs(layout) == RenewRuns()  # no file: no run, no failure
    renew_runs(layout, "refused")
    first = read_renew_runs(layout)
    assert first == RenewRuns(NOW - timedelta(hours=1), "refused", 1)
    renew_runs(layout, "dry")
    assert read_renew_runs(layout) == first  # a dry run changes neither
    renew_runs(layout, "not_needed")
    assert read_renew_runs(layout) == RenewRuns(NOW - timedelta(hours=1), "not_needed", 0)
    path = stamp_renewal_path(layout)
    state = json.loads(path.read_bytes())
    write(path, {**state, "failures_in_a_row": True, "last_run": {"at": 5, "outcome": 3}})
    assert read_renew_runs(layout) == RenewRuns(None, None, None)
    write(path, {"tokens": []})
    assert read_renew_runs(layout) == RenewRuns(None, None, 0)
    for damaged in ("{", "[]", '"x"'):
        path.write_text(damaged, encoding="utf-8")
        assert read_renew_runs(layout) is None, damaged


# -- R20-cert -------------------------------------------------------------------------------------


def test_stamp_cert_critical_when_revoked(check: Any, layout: Layout) -> None:
    renewals(layout, ("due", NOW + timedelta(days=10)))
    revoked_day(layout)
    alert = check()[CERT_KEY]
    assert (alert.rule, alert.severity) == ("R20-cert", "critical")
    assert alert.details == {
        "revoked": 1,
        "expiring": 1,
        "uncovered": 0,
        "title_en": "Time-stamp certificate on a revocation list: 1 certificate(s)",
    }
    assert alert.title == "Zaman damgası sertifikası iptal listesinde: 1 sertifika"
    assert "Örnek" not in alert.title


def test_stamp_cert_high_when_due_within_30_days(check: Any, layout: Layout) -> None:
    """``high`` for a ``due`` token with at most :data:`CERT_EXPIRY_WARNING` (30 days) left
    (manager answer on #117, W5-C-12)."""
    assert timedelta(days=30) == CERT_EXPIRY_WARNING
    renewals(
        layout,
        ("due", NOW + timedelta(days=31)),
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
    assert alert.details["uncovered"] == 0
    assert alert.title == (
        "Zaman damgası sertifikası: 2 damganın sertifikası 30 gün içinde sona eriyor, 0 "
        "damganınki yeniden damgalanmadan sona erdi"
    )
    assert alert.details["title_en"] == (
        "Time-stamp certificate: 2 stamp(s) expire within 30 days, 0 expired without re-stamping"
    )
    revoked_day(layout)
    assert check()[CERT_KEY].severity == "critical"  # a revocation raises it


def test_stamp_cert_high_when_a_certificate_expired_uncovered(check: Any, layout: Layout) -> None:
    """An ``uncovered`` token (its certificate expired with no renewal that counts) is ``high``
    however long ago it expired (manager answer on #117, W5-C-12)."""
    renewals(layout, ("due", NOW + timedelta(days=31)), ("uncovered", NOW - timedelta(days=90)))
    alert = check()[CERT_KEY]
    assert (alert.rule, alert.severity) == ("R20-cert", "high")
    assert alert.details == {
        "revoked": 0,
        "expiring": 0,
        "uncovered": 1,
        "title_en": (
            "Time-stamp certificate: 0 stamp(s) expire within 30 days, 1 expired without "
            "re-stamping"
        ),
    }
    assert alert.title == (
        "Zaman damgası sertifikası: 0 damganın sertifikası 30 gün içinde sona eriyor, 1 "
        "damganınki yeniden damgalanmadan sona erdi"
    )
    renewals(layout, ("covered", NOW - timedelta(days=90)))  # a renewal that counts
    assert CERT_KEY not in check()


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
    check: Any, layout: Layout, config: Config, db_session: Session, ca_sealed: Stamps
) -> None:
    # C22: a root's list, 90 days old and valid for months, fetched yesterday is fresh
    crl_lists(ca_sealed, 90, valid_days=120, fetched_days_ago=1)
    assert CRL_KEY not in check()
    # a fetch exactly seven days old is not "newer than seven days"
    crl_lists(ca_sealed, 90, valid_days=120, fetched_days_ago=7)
    alert = check()[CRL_KEY]
    assert (alert.rule, alert.severity) == ("R20-crl", "medium")
    assert alert.title == "7 günden yeni doğrulanmış bir iptal listesi (SİL) yok"
    assert alert.details == {
        "stale_issuers": 1,
        "failures": 0,
        "changed_files": 0,
        "newest_fetched_at": isoformat_site(NOW - timedelta(days=7), "seconds"),
        "max_age_days": 7,
        "title_en": "No verified revocation list newer than 7 days",
    }
    crl_index_path(layout).write_text("{", encoding="utf-8")
    assert CRL_KEY in check()  # unreadable lists resolve nothing
    crl_lists(ca_sealed, 1)
    assert CRL_KEY not in check()
    assert resolved(db_session, CRL_KEY).details["auto_resolved"]["reason"] == "crl_fresh"
    # a list past its own nextUpdate is stale, though it was fetched just now
    crl_lists(ca_sealed, 8, fetched_days_ago=0)
    assert CRL_KEY in check()
    crl_lists(ca_sealed, 1, fetched_days_ago=2)
    assert CRL_KEY not in check()
    # issued two days ago: nextUpdate alone
    crl_lists(ca_sealed, 2, valid_days=1, fetched_days_ago=0)
    assert CRL_KEY in check()
    crl_lists(ca_sealed, 1, fetched_days_ago=2)
    assert CRL_KEY not in check()

    # tsa.crl_max_age_days (Wave 5 contract 2.5) sets the age of the last fetch.
    strict = config.model_copy(
        update={"tsa": config.tsa.model_copy(update={"crl_max_age_days": 1})}
    )
    assert CRL_KEY in check(using=strict)
    assert active(db_session)[CRL_KEY].details["max_age_days"] == 1


def test_stamp_crl_needs_an_issuer_in_use(check: Any, layout: Layout) -> None:
    """Before the first sealed day no certificate is in use, and no list has to be fresh."""
    write(crl_index_path(layout), {"schema": CRL_INDEX_SCHEMA, "lists": [], "failures": []})
    assert CRL_KEY not in check()


def test_stamp_crl_not_raised_for_arcilogger_test_root(
    monkeypatch: pytest.MonkeyPatch, check: Any, db_session: Session, sealed: Stamps
) -> None:
    """W5-E-5: on a mock or trial install the stamps chain to ARCILOGGER's test root, which
    publishes no list; a test stamp never counts (W5-A D5), so it has no issuer in use. An
    R20-crl raised before (as every mock install had one) is resolved."""
    monkeypatch.setattr(system, "is_test_stamp", lambda policy, root_subject: False)
    assert check()[CRL_KEY].details["stale_issuers"] == 1  # as before W5-E-5
    monkeypatch.undo()
    assert CRL_KEY not in check()  # no index
    assert resolved(db_session, CRL_KEY).details["auto_resolved"]["reason"] == "crl_fresh"
    crl_lists(sealed, None, failures=(FAILURE,))
    assert CRL_KEY not in check()  # every fetch fails
    crl_lists(sealed, 9, fetched_days_ago=8)
    assert CRL_KEY not in check()  # a stale list of the test root


def test_stamp_crl_raised_for_a_real_root_without_a_list(check: Any, ca_sealed: Stamps) -> None:
    """W5-E-5 leaves out only ARCILOGGER's test organization: a root outside it, without any
    stored list, is still an issuer in use."""
    assert TEST_ORGANIZATION not in ca_sealed.root.subject.rfc4514_string()
    alert = check()[CRL_KEY]
    assert (alert.rule, alert.details["stale_issuers"]) == ("R20-crl", 1)


def test_stamp_crl_raised_for_an_issuer_in_use_without_any_list(
    check: Any, ca_sealed: Stamps
) -> None:
    """The worst case: no fetch has stored a list for the stamps' root yet, or every URL
    fails (only ``failures`` in the index)."""
    assert check()[CRL_KEY].details["stale_issuers"] == 1  # no index yet
    crl_lists(ca_sealed, None, failures=(FAILURE, {**FAILURE, "error": "unknown_issuer"}))
    alert = check()[CRL_KEY]
    assert (alert.details["stale_issuers"], alert.details["failures"]) == (1, 2)
    assert alert.details["newest_fetched_at"] is None


def test_stamp_crl_after_a_fetch_without_a_list_address(
    monkeypatch: pytest.MonkeyPatch, check: Any, layout: Layout, db_session: Session
) -> None:
    """#151: ``crl fetch`` with no list address known writes an index with no list and no
    failure. Before the first sealed day no issuer is in use, so it raises nothing, and an open
    alert is resolved: the lists can be read."""
    run = crl_fetch(CrlOptions(), layout, [], NOW)
    assert run.exit_code == 2
    assert json.loads(crl_index_path(layout).read_bytes()) == {
        "schema": CRL_INDEX_SCHEMA,
        "lists": [],
        "failures": [],
    }
    monkeypatch.setattr(system, "issuers_in_use", lambda stamp: frozenset({"0" * 64}))
    assert check()[CRL_KEY].details["stale_issuers"] == 1  # test: an issuer in use
    monkeypatch.undo()
    assert CRL_KEY not in check()
    assert resolved(db_session, CRL_KEY).details["auto_resolved"]["reason"] == "crl_fresh"


def test_stamp_crl_raised_after_a_fetch_without_a_list_address_while_in_use(
    check: Any, ca_sealed: Stamps
) -> None:
    """The made-up CA names no list address and ``tsa.crl_urls`` is empty: the empty index the
    fetch writes is no list, and the issuer in use is stale."""
    layout = ca_sealed.layout
    run = crl_fetch(CrlOptions(), layout, [ca_sealed.root], NOW)
    assert (run.urls, run.exit_code) == ((), 2)
    index = json.loads(crl_index_path(layout).read_bytes())
    assert (index["lists"], index["failures"]) == ([], [])
    alert = check()[CRL_KEY]
    assert (alert.details["stale_issuers"], alert.details["failures"]) == (1, 0)
    assert alert.details["newest_fetched_at"] is None


def test_stamp_crl_judged_from_the_index_not_an_exit_code(check: Any, ca_sealed: Stamps) -> None:
    """C12: the fetch is judged from ``state/crl``. Failed fetches raise nothing while the
    stored list's last fetch is recent (C22); once it is old the alert counts them from the
    index."""
    crl_lists(ca_sealed, 4, fetched_days_ago=4, failures=(FAILURE, FAILURE))
    assert CRL_KEY not in check()
    crl_lists(ca_sealed, 9, fetched_days_ago=8, failures=(FAILURE,))
    alert = check()[CRL_KEY]
    assert alert.details["failures"] == 1
    assert alert.details["newest_fetched_at"] == isoformat_site(NOW - timedelta(days=8), "seconds")


def test_stamp_crl_a_changed_list_is_no_verified_list(check: Any, ca_sealed: Stamps) -> None:
    """C14: a fresh list whose file no longer holds its bytes does not count."""
    name = crl_lists(ca_sealed, 1)
    assert name is not None
    assert CRL_KEY not in check()
    path = ca_sealed.layout.crl_dir / name
    path.write_bytes(path.read_bytes() + b"\x00")  # test: the stored file is changed
    alert = check()[CRL_KEY]
    assert (alert.details["stale_issuers"], alert.details["changed_files"]) == (1, 1)


def test_stamp_cert_critical_from_the_stored_lists(check: Any, sealed: Stamps) -> None:
    """The newest stamp's chain against the stored lists: a revocation before it is raised
    before the next scheduled verification finds it; one after it is not ``crl-revoked``."""
    crl_lists(sealed, 0, revoked_at=NOW - timedelta(hours=1))  # after the stamp of 31.10.
    assert CERT_KEY not in check()
    crl_lists(sealed, 0, revoked_at=at_site(date(2026, 10, 30), 12, 0))
    alert = check()[CERT_KEY]
    assert (alert.severity, alert.details["revoked"]) == ("critical", 1)
    assert "Mock" not in alert.title + json.dumps(alert.details)


def test_stamp_cert_from_the_lists_stays_open_while_they_cannot_be_read(
    check: Any, layout: Layout, sealed: Stamps, db_session: Session
) -> None:
    """W5-C-7: an input that cannot be read decides nothing. A revocation found from the stored
    lists (before a scheduled verification records it) is not resolved while the index, the
    list's own file (changed or gone) or the newest sealed day's token it was checked against
    cannot be read."""
    renewals(layout, ("not_due", NOW + timedelta(days=400)))  # a readable report
    name = crl_lists(sealed, 0, revoked_at=at_site(date(2026, 10, 30), 12, 0))
    assert check()[CERT_KEY].severity == "critical"
    index = crl_index_path(layout)
    saved_index = index.read_bytes()
    index.write_text("{", encoding="utf-8")  # damaged: the lists cannot be read
    assert check()[CERT_KEY].severity == "critical"
    index.write_bytes(saved_index)
    token = layout.token(date(2026, 10, 30))
    saved_token = token.read_bytes()
    sealed.vault.tamper(token, b"\x00")  # test: the token in use cannot be read
    assert check()[CERT_KEY].severity == "critical"
    token.write_bytes(saved_token)
    assert check()[CERT_KEY].severity == "critical"  # readable again: still revoked
    stored = layout.crl_dir / str(name)
    saved_list = stored.read_bytes()
    stored.write_bytes(saved_list + b"\x00")  # changed (C14): left out, a person has to look
    assert check()[CERT_KEY].severity == "critical"
    stored.unlink()  # gone
    assert check()[CERT_KEY].severity == "critical"
    stored.write_bytes(saved_list)
    assert check()[CERT_KEY].severity == "critical"
    crl_lists(sealed, 0)  # a list without the revocation
    assert CERT_KEY not in check()
    reason = resolved(db_session, CERT_KEY).details["auto_resolved"]["reason"]
    assert reason == "certificate_ok"


def test_stamp_cert_expiry_resolves_although_a_list_was_changed(
    check: Any, layout: Layout, sealed: Stamps, db_session: Session
) -> None:
    """A changed or deleted list (C14/C20) keeps its index entry for good, so it holds open only
    an alert that carries a revocation (manager's re-review of #117): an expiry alert resolves
    once ``renewal.json`` says the token is covered, a revocation alert does not, even after a
    later expiry finding wrote ``revoked`` 0 into its details."""
    renewals(layout, ("due", NOW + timedelta(days=12)))
    name = crl_lists(sealed, 0)
    alert = check()[CERT_KEY]
    assert (alert.severity, alert.details["revoked"]) == ("high", 0)
    stored = layout.crl_dir / str(name)
    saved_list = stored.read_bytes()
    stored.write_bytes(saved_list + b"\x00")  # test: changed, its index entry stays
    assert check()[CERT_KEY].severity == "high"
    renewals(layout, ("covered", NOW + timedelta(days=12)))
    assert CERT_KEY not in check()
    reason = resolved(db_session, CERT_KEY).details["auto_resolved"]["reason"]
    assert reason == "certificate_ok"
    stored.unlink()  # gone: no hold on a new expiry alert either
    renewals(layout, ("due", NOW + timedelta(days=12)))
    assert CERT_KEY in check()
    renewals(layout, ("covered", NOW + timedelta(days=12)))
    assert CERT_KEY not in check()

    # a revocation from the stored lists stays open once its list is changed
    stored.write_bytes(saved_list)
    crl_lists(sealed, 0, revoked_at=at_site(date(2026, 10, 30), 12, 0))
    renewals(layout, ("not_due", NOW + timedelta(days=400)))
    assert check()[CERT_KEY].details["revoked"] == 1
    revoking = layout.crl_dir / str(
        crl_lists(sealed, 0, revoked_at=at_site(date(2026, 10, 30), 12, 0))
    )
    revoking.write_bytes(revoking.read_bytes() + b"\x00")  # the revocation is no longer seen
    renewals(layout, ("due", NOW + timedelta(days=12)))
    alert = check()[CERT_KEY]
    assert (alert.severity, alert.details["revoked"]) == ("critical", 0)  # the store keeps it
    renewals(layout, ("covered", NOW + timedelta(days=12)))
    assert check()[CERT_KEY].severity == "critical"  # still held: it carries a revocation


def test_stamp_cert_from_a_verification_stays_open_while_it_cannot_be_read(
    check: Any, layout: Layout, sealed: Stamps, db_session: Session
) -> None:
    """W5-C-7: a revocation that only a scheduled verification recorded is not resolved while
    that ``last-*.json`` cannot be read or holds no verification."""
    renewals(layout, ("not_due", NOW + timedelta(days=400)))
    crl_lists(sealed, 0)  # the stored lists do not have it yet
    revoked_day(layout)
    assert check()[CERT_KEY].severity == "critical"
    report = layout.state / "verify" / "last-day.json"
    saved = report.read_bytes()
    for damaged in ("{", "[]", '{"at": null, "checks": []}', '{"at": "2026-11-01T00:00:00Z"}'):
        report.write_text(damaged, encoding="utf-8")
        assert check()[CERT_KEY].severity == "critical", damaged
    report.write_bytes(saved)
    assert check()[CERT_KEY].severity == "critical"
    write(report, {"at": isoformat_site(NOW, "seconds"), "deep": False, "checks": []})
    assert CERT_KEY not in check()  # a readable verification without it
    reason = resolved(db_session, CERT_KEY).details["auto_resolved"]["reason"]
    assert reason == "certificate_ok"


# -- the e-mails ----------------------------------------------------------------------------------


def test_legal_alert_mails_carry_no_names_and_no_log_lines(
    check: Any, layout: Layout, ca_sealed: Stamps
) -> None:
    renewals(layout, ("due", NOW + timedelta(days=10)), ("uncovered", NOW - timedelta(days=1)))
    revoked_day(layout)
    crl_lists(ca_sealed, 9, failures=(FAILURE,))
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
            "Mock TSA",
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
        for hidden in ("Örnek", "Mock", "192.0.2.", "manifest-", ".crl"):
            assert hidden not in shown, hidden
    # What an e-mail and the alert detail can say of the three codes: the catalogue line, the
    # facts of the details and the runbook.
    for item in items:
        text = rule_text(item.rule)
        catalogue = (
            text.name_tr, text.name_en, text.action_tr, text.action_en,
            *describe(item.rule, item.details), *runbook(item.rule),
        )  # fmt: skip
        for line in catalogue:
            for hidden in ("Örnek", "Test TSA", "Mock TSA", "CN=", "192.0.2.", "manifest-"):
                assert hidden not in line, (item.rule, hidden)
            for claim in ("uyumlu", "garanti", "compliant"):
                assert claim not in line.lower(), (item.rule, claim)


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


def test_the_wave5_alert_codes_are_switched_on() -> None:
    """D14: the switch-on package W5-E added the whole-string constants ``R20-renewal``,
    ``R20-cert`` and ``R20-crl`` with their catalogue texts, runbooks and first-steps guide rows
    (``tests/test_docs_consistency.py::test_first_steps_explain_every_alert_code``), and
    registered the three checks, last. Only the alerts' own modules spell a whole code."""
    found = {Path(where.split(":", 1)[0]).as_posix() for where in _code_constants()}
    assert found == {
        "worker/src/arcilogger_worker/alerts/system.py",
        "worker/src/arcilogger_worker/alerts/texts.py",
    }
    assert {RENEWAL_RULE, CERT_RULE, CRL_RULE} == WAVE5_CODES
    assert KEYS == ("R20-renewal:none", "R20-cert:none", "R20-crl:none")
    assert tuple(system._rule_of(key) for key in KEYS) == (RENEWAL_RULE, CERT_RULE, CRL_RULE)
    assert REGISTERED[-len(LEGAL_CHECKS) :] == LEGAL_CHECKS
    assert len({name for name, _ in REGISTERED}) == len(REGISTERED)
    for code in sorted(WAVE5_CODES):
        assert code in RULES, code
        assert code in DESCRIBERS, code
        assert runbook(code) != UNKNOWN_RUNBOOK, code
    # The texts' 30 days are alerts.system's (which imports alerts.texts).
    expiring = describe(CERT_RULE, {"revoked": 0, "expiring": 1, "uncovered": 0})
    assert all(f"{CERT_EXPIRY_WARNING.days} " in sentence for sentence in expiring)
    assert f"{CERT_EXPIRY_WARNING.days} gün" in runbook(CERT_RULE)[0]
    # So is the number of failed Renew runs that raises R20-renewal.
    for failures, told in ((RENEW_FAILURES_ALERT, True), (RENEW_FAILURES_ALERT - 1, False)):
        facts = describe(RENEWAL_RULE, {"due": 0, "uncovered": 0, "failures_in_a_row": failures})
        assert ("Renew" in facts[0] and "Renew" in facts[1]) is told, failures


def test_registered_checks_raise_and_clear_the_legal_alerts(
    monkeypatch: pytest.MonkeyPatch,
    check: Any,
    layout: Layout,
    db_session: Session,
    ca_sealed: Stamps,
) -> None:
    """Wave 5 contract §3 W5-C, "alert raise/clear for the three codes (after W5-E)": with every
    registered check, as the worker runs them."""
    monkeypatch.setattr(system, "CHECKS", REGISTERED)
    renewals(layout, ("due", NOW + timedelta(days=10)))
    revoked_day(layout)
    crl_lists(ca_sealed, 9, failures=(FAILURE,))
    alerts = check()
    assert {key: (alerts[key].rule, alerts[key].severity) for key in alerts if key in KEYS} == {
        RENEWAL_KEY: ("R20-renewal", "high"),
        CERT_KEY: ("R20-cert", "critical"),
        CRL_KEY: ("R20-crl", "medium"),
    }
    renewals(layout, ("covered", NOW + timedelta(days=10)))
    write(
        layout.state / "verify" / "last-day.json",
        {"at": isoformat_site(NOW, "seconds"), "deep": False, "checks": []},
    )
    crl_lists(ca_sealed, 1)
    assert set(check()) & set(KEYS) == set()
    reasons = {key: resolved(db_session, key).details["auto_resolved"]["reason"] for key in KEYS}
    assert reasons == {
        RENEWAL_KEY: "renewal_ok",
        CERT_KEY: "certificate_ok",
        CRL_KEY: "crl_fresh",
    }
