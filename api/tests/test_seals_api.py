"""The sealing page through the API (spec 6.6-6.9, 8.4, 12.3 page 9): calendar states, the day
detail against the sealed manifest, downloads, on-demand verification and its time limit."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import sealcheck
from arcilogger_api.routers import seals as seals_router
from arcilogger_api.sealcheck import CheckResult, read_roots, run_check
from arcilogger_api.sealinfo import VaultView
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.immutability import unlock
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_core.tsa.mock import MockFailure
from arcilogger_core.tsa.verify import inspect_token
from arcilogger_db.models import AuditLog, SealVerification
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

API = "/api/v1/seals"
D10, D11, D12, D13, D14 = (date(2026, 9, day) for day in range(10, 15))
Login = Callable[..., LoggedIn]


@pytest.fixture
def vault(api_config: Config) -> Iterator[SealedVault]:
    """10 and 11 sealed, 12 sealed three days late, 13 failed (TSA down), 14 waiting."""
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(D10 - timedelta(days=30)))
    sealed.seal_days([D10, D11])
    sealed.write_day(D12)
    assert sealed.seal(D12, now=at_site(D12 + timedelta(days=3), 1, 0)).status == "sealed"
    sealed.write_day(D13)
    assert sealed.fail_seal(D13).status == "pending"
    sealed.write_day(D14)
    yield sealed
    sealed.unlock_all()


def cells(client: TestClient, month: str) -> dict[date, dict[str, Any]]:
    response = client.get(f"{API}/calendar", params={"month": month})
    assert response.status_code == 200, response.text
    return {date.fromisoformat(cell["day"]): cell for cell in response.json()["days"]}


def error_code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


# -- calendar -------------------------------------------------------------------------------------


def test_calendar_states(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout
) -> None:
    login_as(client, "auditor")
    days = cells(client, "2026-09")
    assert len(days) == 30
    assert days[D10]["state"] == "sealed"  # sealed, never verified since
    assert days[D10]["tone"] == "warning"
    assert days[D10]["files"] == 2
    assert days[D10]["lines"] == 72
    assert days[D10]["serial"]
    assert days[D10]["replica"] == "disabled"
    assert days[D12]["state"] == "late"
    assert days[D12]["stamped_late"] is True
    assert days[D13]["state"] == "failed"
    assert days[D13]["tone"] == "error"
    # spec 12.4 microcopy from the error code, never the English exception text; the mock TSA
    # (the test configuration, its URL the default Kamu SM test host) is never called Kamu SM.
    assert days[D13]["message_tr"] == (
        "Mühürleme başarısız: zaman damgası sunucusuna ulaşılamadı. Bir sonraki mühürleme "
        "çalışmasında tekrar denenecek."
    )
    assert days[D13]["error_detail"] == "mock TSA unreachable (injected)"

    assert days[D13]["files"] is None  # the failed attempt's manifest is not a seal
    assert days[D13]["overdue"] is True
    assert days[D14]["state"] == "pending"
    assert days[D14]["message_tr"] is None
    assert days[date(2026, 9, 9)]["state"] == "no_data"
    assert days[date(2026, 9, 30)]["state"] == "no_data"
    body = client.get(f"{API}/calendar", params={"month": "2026-09"}).json()
    assert body["counts"] == {"sealed": 2, "late": 1, "failed": 1, "pending": 1, "no_data": 25}
    assert body["recent_verify_days"] == 35
    assert {item["state"] for item in body["legend"]} >= {"verified", "deleted", "open"}
    assert body["problems"] == []

    # A scheduled verification (the real task command) turns a day green.
    result = vault.core("verify", "--day", D10.isoformat())
    assert result.returncode == 0, result.stdout + result.stderr
    days = cells(client, "2026-09")
    assert days[D10]["state"] == "verified"
    assert days[D10]["tone"] == "ok"
    assert days[D10]["verification"]["source"] == "scheduled"
    assert days[D11]["state"] == "sealed"


def test_current_month_has_today_open_and_future_days(
    client: TestClient, login_as: Login, layout: Layout
) -> None:
    login_as(client, "admin")
    today = site_day(utc_now())
    response = client.get(f"{API}/calendar")
    assert response.status_code == 200
    body = response.json()
    assert body["month"] == today.strftime("%Y-%m")
    days = {date.fromisoformat(cell["day"]): cell for cell in body["days"]}
    assert days[today]["state"] == "open"
    tomorrow = today + timedelta(days=1)
    if tomorrow.month == today.month:
        assert days[tomorrow]["state"] == "future"
    assert error_code(client.get(f"{API}/calendar", params={"month": "2026-13"})) == (
        422,
        "validation_error",
    )


def test_deleted_days_after_retention(
    client: TestClient, login_as: Login, api_config: Config, tmp_path: Path
) -> None:
    days = [date(2024, 9, 10) + timedelta(days=n) for n in range(4)]
    vault = make_vault(api_config.data_dir, tsa_now=at_site(days[0] - timedelta(days=30)))
    try:
        vault.seal_days(days)
        result = vault.retention(at_site(date(2026, 9, 12), 1, 0))
        assert result.deleted == days[:2], result.error
        login_as(client, "auditor")
        month = cells(client, "2024-09")
        assert [month[day]["state"] for day in days] == ["deleted", "deleted", "sealed", "sealed"]
        assert month[days[0]]["tone"] == "neutral"
        detail = client.get(f"{API}/{days[0].isoformat()}").json()
        assert detail["sealed"] is False
        assert detail["deleted"]["record"].startswith("vault/deletions/")
        assert detail["deleted"]["manifest_sha256"]
        # The first remaining day links to the chain anchor of the deleted range.
        kept = client.get(f"{API}/{days[2].isoformat()}").json()
        assert kept["previous"]["day"] == days[1].isoformat()
        assert kept["previous"]["target"] == "chain_anchor"
        assert kept["previous"]["matches"] is True
    finally:
        vault.unlock_all()


def test_unreadable_state_files_do_not_break_the_page(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout
) -> None:
    (layout.state / "verify").mkdir(parents=True, exist_ok=True)
    (layout.state / "verify" / "last-full.json").write_bytes(b"{broken")
    layout.seal_state(D14).write_bytes(b"\xff\xfe not json")
    login_as(client, "admin")
    response = client.get(f"{API}/calendar", params={"month": "2026-09"})
    assert response.status_code == 200
    body = response.json()
    assert "last-full.json: JSONDecodeError" in body["problems"]
    days = {cell["day"]: cell for cell in body["days"]}
    assert days[D14.isoformat()]["state"] == "failed"
    assert "Durum dosyası okunamıyor" in days[D14.isoformat()]["message_tr"]


def test_a_stamped_day_the_seal_task_did_not_finish_is_not_shown_sealed(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout
) -> None:
    """Token in the vault, but locking the files (or the post-stamp check) failed: the Seal
    task does not count the day as sealed, and neither does the page."""
    path = layout.seal_state(D11)
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(
        status="pending",
        last_error="[WinError 5] Access is denied (lock)",
        next_attempt_at="2026-09-12T00:45:00+03:00",
    )
    path.write_text(json.dumps(state), encoding="utf-8")
    login_as(client, "admin")
    cell = cells(client, "2026-09")[D11]
    assert cell["state"] == "failed"
    assert cell["tone"] == "error"
    assert cell["message_tr"] == (
        "Zaman damgası alındı, ancak mühürleme görevi günü tamamlayamadı (teknik ayrıntı gün "
        "sayfasında). Sonraki deneme: 12.09.2026 00:45:00."
    )
    assert cell["error_detail"] == "[WinError 5] Access is denied (lock)"
    assert cell["message_en"].startswith("The time stamp was obtained, but sealing did not")
    assert cell["overdue"] is True
    assert cell["files"] == 2  # the stamped manifest is still shown
    detail = client.get(f"{API}/{D11.isoformat()}").json()
    assert detail["state"] == "failed"
    assert detail["sealed"] is True  # manifest and token are in the vault
    assert detail["seal_state"]["last_error"] == "[WinError 5] Access is denied (lock)"

    state.update(last_error=None, next_attempt_at=None)
    path.write_text(json.dumps(state), encoding="utf-8")
    cell = cells(client, "2026-09")[D11]
    assert cell["state"] == "pending"
    assert cell["message_en"] == (
        "The time stamp was obtained; the sealing task has not finished the day yet."
    )
    path.write_bytes(b"[]")
    cell = cells(client, "2026-09")[D11]
    assert cell["state"] == "failed"
    assert cell["message_tr"] == "Durum dosyası okunamıyor: 2026-09-11.json: not a seal state"


def test_a_failed_retention_run_deletes_nothing_on_the_page(
    client: TestClient, login_as: Login, api_config: Config
) -> None:
    """A run whose time stamp failed (the TSA was down) or did not verify leaves no record; what
    an interrupted run or an earlier version may leave (a ``.part`` record and token, a record
    without its time stamp) deleted nothing either: the days stay sealed."""
    days = [date(2024, 9, 10) + timedelta(days=n) for n in range(4)]
    vault = make_vault(api_config.data_dir, tsa_now=at_site(days[0] - timedelta(days=30)))
    try:
        vault.seal_days(days)
        vault.tsa.failure = MockFailure.UNREACHABLE
        unreachable = vault.retention(at_site(date(2026, 9, 12), 1, 0))
        assert unreachable.error is not None
        assert "could not be time-stamped" in unreachable.error
        vault.tsa.failure = MockFailure.BAD_SIGNATURE
        result = vault.retention(at_site(date(2026, 9, 13), 1, 0))
        assert result.error is not None
        assert result.error.startswith("İmha tutanağının zaman damgası doğrulanamadı")
        assert sorted(p.name for p in vault.layout.deletions.iterdir()) == []
        # Leftovers: an interrupted stamp, and an unstamped record of an earlier version.
        listing = json.dumps({"days": [{"day": day.isoformat()} for day in days[:3]]})
        folder = vault.layout.deletions
        (folder / "2026-09-13-imha.json.part").write_text(listing, encoding="utf-8")
        (folder / "2026-09-13-imha.json.part.zd").write_bytes(b"token of the part")
        (folder / "2026-09-12-imha.json").write_text(listing, encoding="utf-8")
        login_as(client, "auditor")
        month = cells(client, "2024-09")
        assert [month[day]["state"] for day in days] == ["sealed"] * 4
        for day in days[:3]:
            detail = client.get(f"{API}/{day.isoformat()}").json()
            assert detail["sealed"] is True
            assert detail["deleted"] is None
    finally:
        vault.unlock_all()


# -- day detail and downloads ---------------------------------------------------------------------


def test_day_detail_equals_the_sealed_manifest(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout, tmp_path: Path
) -> None:
    vault.replicate(D11, tmp_path / "nas")
    login_as(client, "auditor")
    response = client.get(f"{API}/{D11.isoformat()}")
    assert response.status_code == 200, response.text
    detail = response.json()
    manifest_path = layout.manifest(D11)
    manifest = json.loads(manifest_path.read_bytes())
    assert detail["sealed"] is True
    assert detail["state"] == "sealed"
    assert detail["manifest"]["sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert detail["manifest"]["path"] == "vault/2026/09/11/manifest-2026-09-11.json"
    assert detail["manifest"]["created_at"] == manifest["created_at"]
    assert detail["manifest"]["tsa"] == manifest["tsa"]
    assert detail["files"] == [
        {key: entry[key] for key in detail["files"][0]} for entry in manifest["files"]
    ]
    assert [f["source"] for f in detail["files"]] == ["core-r1", "fortigate"]
    assert detail["totals"]["lines"] == sum(f["lines"] for f in manifest["files"]) == 72
    assert detail["audit_log"] == manifest["audit_log"]
    assert detail["gaps"] == manifest["gaps"]
    assert detail["clock"] == manifest["clock"]
    previous = manifest["previous"]
    assert detail["previous"] == {**previous, "matches": True, "target": "vault"}
    token = inspect_token(layout.token(D11).read_bytes())
    assert detail["token"]["serial"] == token.serial
    assert detail["token"]["policy"] == token.policy
    assert detail["token"]["tsa_name"] == token.tsa_name
    assert detail["token"]["provider"] == "mock"
    assert detail["token"]["legal_grade"] is False
    assert detail["token"]["legal_grade_claimed"] is False  # a test stamp, and says so
    assert datetime.fromisoformat(detail["token"]["gen_time"]) == token.gen_time
    assert detail["token"]["tsa_cert_not_before"] < detail["token"]["tsa_cert_not_after"]
    assert detail["token"]["check"]["ok"] is True
    assert "Mock Root" in detail["token"]["check"]["root"]
    assert detail["replica"]["status"] == "ok"
    assert detail["replica"]["files"] == 5
    assert detail["seal_state"]["status"] == "sealed"
    assert detail["verification"] is None

    first = client.get(f"{API}/{D10.isoformat()}").json()
    assert first["previous"] is None  # the chain starts here
    failed = client.get(f"{API}/{D13.isoformat()}").json()
    assert failed["sealed"] is False
    assert failed["manifest"] is None
    assert failed["seal_state"]["attempts"] == 1
    assert failed["seal_state"]["last_error"]


def test_day_detail_shows_a_broken_link(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout
) -> None:
    vault.tamper(layout.token(D10), b"\x00")
    login_as(client, "admin")
    detail = client.get(f"{API}/{D11.isoformat()}").json()
    assert detail["previous"]["matches"] is False
    changed = client.get(f"{API}/{D10.isoformat()}").json()
    assert changed["token"]["check"]["ok"] is False
    # The token check's messages in Turkish as well (finding 8).
    check = changed["token"]["check"]
    assert check["errors"]
    assert len(check["errors_tr"]) == len(check["errors"])
    assert check["errors_tr"][0].startswith("zaman damgası dosyası okunamıyor")


def test_the_token_facts_keep_the_claim_and_the_test_stamp_apart(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout
) -> None:
    """Decision 5: a mock stamp is a test stamp without a production claim (``legal_grade`` is
    only ever the verified chain's)."""
    login_as(client, "admin")
    token = client.get(f"{API}/{D10.isoformat()}").json()["token"]
    assert token["legal_grade"] is False
    assert token["legal_grade_claimed"] is False
    assert token["test_stamp"] is True
    assert token["check"] == {
        "ok": True,
        "errors": [],
        "errors_tr": [],
        "root": token["check"]["root"],
    }
    # A manifest that claims the production server on a test stamp: the claim is shown as a
    # claim, the stamp stays a test stamp, and nothing is legal grade.
    manifest = layout.manifest(D10)
    unlock(manifest)
    data = json.loads(manifest.read_bytes())
    data["tsa"]["legal_grade"] = True
    manifest.write_bytes(json.dumps(data).encode())
    claimed = client.get(f"{API}/{D10.isoformat()}").json()["token"]
    assert (claimed["legal_grade_claimed"], claimed["test_stamp"]) == (True, True)
    assert claimed["legal_grade"] is False


@pytest.mark.parametrize(("path", "status"), [("2026-02-30", 422), ("2999-01-01", 404)])
def test_day_must_be_a_real_past_day(
    client: TestClient, login_as: Login, path: str, status: int
) -> None:
    login_as(client, "admin")
    assert client.get(f"{API}/{path}").status_code == status


@pytest.mark.parametrize(("kind", "suffix"), [("manifest", ".json"), ("token", ".json.zd")])
def test_manifest_and_token_downloads_are_audited(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    kind: str,
    suffix: str,
) -> None:
    who = login_as(client, "auditor")
    response = client.get(f"{API}/{D11.isoformat()}/{kind}")
    assert response.status_code == 200
    path = layout.manifest(D11) if kind == "manifest" else layout.token(D11)
    assert response.content == path.read_bytes()
    name = f"manifest-2026-09-11{suffix}"
    assert response.headers["content-disposition"] == f'attachment; filename="{name}"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    (entry,) = audits(app_session_factory, "seal.download")
    assert entry.actor_username == who.username
    assert entry.target == "seal:2026-09-11"
    assert entry.details == {
        "file": name,
        "kind": kind,
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }
    assert client.get(f"{API}/{D14.isoformat()}/{kind}").status_code == 404
    assert client.get(f"{API}/{D13.isoformat()}/{kind}").status_code == 404  # failed attempt


# -- on-demand verification -----------------------------------------------------------------------


def test_verify_now_stores_the_result_and_turns_the_day_green(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
) -> None:
    who = login_as(client, "auditor")
    response = client.post(f"{API}/{D11.isoformat()}/verify")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == "ok"
    assert result["ok"] is True
    assert result["deep"] is True
    assert result["message_tr"] == "11.09.2026 günü doğrulandı."
    assert result["details"]["serial"]
    assert result["details"]["duration_ms"] >= 0
    with app_session_factory() as session:
        row = session.scalars(select(SealVerification)).one()
        assert (row.day, row.outcome, row.username, row.deep) == (D11, "ok", who.username, True)
    (entry,) = audits(app_session_factory, "seal.verify")
    assert entry.target == "seal:2026-09-11"
    assert entry.details["outcome"] == "ok"
    day = cells(client, "2026-09")[D11]
    assert day["state"] == "verified"
    assert day["verification"]["source"] == "on_demand"
    detail = client.get(f"{API}/{D11.isoformat()}").json()
    assert detail["verifications"]["on_demand"]["outcome"] == "ok"
    assert detail["verification"]["by"] == who.username
    quick = client.post(f"{API}/{D11.isoformat()}/verify", params={"deep": "false"}).json()
    assert quick["outcome"] == "ok"
    assert quick["deep"] is False


def test_verify_now_detects_a_changed_vault_file(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    # This test's vault is its own copy: changing it never touches another test's data.
    vault.tamper(layout.vault_day(D11) / "fortigate.log.gz")
    login_as(client, "admin")
    result = client.post(f"{API}/{D11.isoformat()}/verify").json()
    assert result["outcome"] == "failed"
    assert result["ok"] is False
    assert any("fortigate.log.gz: SHA-256 mismatch" in error for error in result["errors"])
    # Turkish for the interface (spec 12.4), the English text of the core stays in ``errors``
    assert (
        "2026/09/11/fortigate.log.gz: SHA-256 özeti tutmuyor (dosya değişmiş)"
        in (result["errors_tr"])
    )
    assert len(result["errors_tr"]) == len(result["errors"])
    assert len(result["warnings_tr"]) == len(result["warnings"])
    assert result["message_tr"].startswith("11.09.2026 günü doğrulanamadı:")
    assert "mismatch" not in result["message_tr"]
    assert "SHA-256 özeti tutmuyor" in result["message_tr"]
    day = cells(client, "2026-09")[D11]
    assert day["state"] == "failed"
    assert "Doğrulama başarısız" in day["message_tr"]
    assert "SHA-256 özeti tutmuyor" in day["message_tr"]
    assert "SHA-256 mismatch" in day["error_detail"]
    (entry,) = audits(app_session_factory, "seal.verify")
    assert entry.details["outcome"] == "failed"
    # The day page shows the stored result in Turkish too (finding 8), later and to everyone.
    detail = client.get(f"{API}/{D11.isoformat()}").json()
    for stored in (detail["verification"], detail["verifications"]["on_demand"]):
        assert stored["errors"] == result["errors"]  # the technical text, as stored
        assert stored["errors_tr"] == result["errors_tr"]
        assert stored["warnings_tr"] == result["warnings_tr"]
        assert not any("mismatch" in text for text in stored["errors_tr"])
        assert not any("read-only" in text for text in stored["warnings_tr"])


def test_a_scheduled_verification_is_shown_in_turkish(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout
) -> None:
    """The Verify task (state/verify) found a changed file: the day page says so in Turkish."""
    vault.tamper(layout.vault_day(D10) / "fortigate.log.gz")
    vault.core("verify", "--day", D10.isoformat())
    login_as(client, "auditor")
    detail = client.get(f"{API}/{D10.isoformat()}").json()
    verification = detail["verification"]
    assert verification["source"] == "scheduled"
    assert verification["ok"] is False
    assert "2026/09/10/fortigate.log.gz: SHA-256 mismatch (file changed)" in verification["errors"]
    assert (
        "2026/09/10/fortigate.log.gz: SHA-256 özeti tutmuyor (dosya değişmiş)"
        in (verification["errors_tr"])
    )
    assert len(verification["errors_tr"]) == len(verification["errors"])
    assert detail["verifications"]["scheduled"]["errors_tr"] == verification["errors_tr"]


def test_verify_now_time_limit_and_errors(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login_as(client, "admin")

    def outcome(name: str) -> Callable[..., CheckResult]:
        return lambda *args, **kwargs: CheckResult(name, errors=[f"{name} happened"])

    monkeypatch.setattr(seals_router, "run_check", outcome("timeout"))
    timed_out = client.post(f"{API}/{D10.isoformat()}/verify").json()
    assert timed_out["outcome"] == "timeout"
    assert "durduruldu" in timed_out["message_tr"]
    monkeypatch.setattr(seals_router, "run_check", outcome("error"))
    assert client.post(f"{API}/{D10.isoformat()}/verify").json()["outcome"] == "error"
    # Inconclusive runs are kept but never turn a day red.
    assert cells(client, "2026-09")[D10]["state"] == "sealed"
    detail = client.get(f"{API}/{D10.isoformat()}").json()
    assert detail["verifications"]["on_demand"]["outcome"] == "error"
    assert detail["verification"] is None


def test_run_check_kills_a_check_that_takes_too_long(vault: SealedVault, layout: Layout) -> None:
    result = run_check(layout, None, D10, deep=True, provider="mock", timeout=0.01)
    assert result.outcome == "timeout"
    assert "time limit" in result.errors[0]
    real = run_check(layout, None, D10, deep=True, provider="mock")
    assert real.outcome == "ok"
    assert real.details["serial"]
    # A leftover mock TSA is not trusted once the provider is a real one.
    production = run_check(layout, None, D10, deep=False, provider="zamane")
    assert production.outcome != "ok"
    assert read_roots(layout, None, provider="zamane") == []
    assert len(read_roots(layout, None, provider="mock")) == 1


def test_run_check_reports_a_broken_child(
    vault: SealedVault, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sealcheck,
        "command",
        lambda *args, **kwargs: [sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    result = run_check(layout, None, D10, deep=False, provider="mock")
    assert result.outcome == "error"
    assert "exit 3" in result.errors[0]


def test_verify_now_records_a_check_that_cannot_start(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The service account may not start python.exe (or the path is wrong under WinSW): the
    attempt is still stored, audited and shown, never a bare 500."""
    missing = str(tmp_path / "nowhere" / "python.exe")
    monkeypatch.setattr(sealcheck, "command", lambda *args, **kwargs: [missing, "-I"])
    who = login_as(client, "admin")
    response = client.post(f"{API}/{D10.isoformat()}/verify")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == "error"
    (error,) = result["errors"]  # the reason is the operating system's text
    assert error.startswith("the verification process could not start: ")
    assert len(error) > len("the verification process could not start: ")
    assert result["message_en"] == "The verification could not run; details are in the system log."
    with app_session_factory() as session:
        row = session.scalars(select(SealVerification)).one()
        assert (row.outcome, row.username, row.errors) == ("error", who.username, result["errors"])
    (entry,) = audits(app_session_factory, "seal.verify")
    assert entry.details["outcome"] == "error"
    assert entry.details["errors"] == result["errors"]
    assert cells(client, "2026-09")[D10]["state"] == "sealed"  # inconclusive: never red


def test_sealcheck_child_prints_the_day_check(
    vault: SealedVault, layout: Layout, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = sealcheck.main(
        ["--data-dir", str(layout.root), "--day", D11.isoformat(), "--deep", "--provider", "mock"]
    )
    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["ok"] is True
    assert printed["deep"] is True
    assert printed["day"] == D11.isoformat()
    empty = Layout(tmp_path / "empty")
    code = sealcheck.main(["--data-dir", str(empty.root), "--day", D11.isoformat()])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["errors"] == ["no trusted root certificates"]


def test_verify_refusals(
    client: TestClient, login_as: Login, vault: SealedVault, layout: Layout
) -> None:
    login_as(client, "admin")
    assert error_code(client.post(f"{API}/{D14.isoformat()}/verify")) == (404, "not_found")
    assert error_code(client.post(f"{API}/{D13.isoformat()}/verify")) == (404, "not_found")
    assert seals_router._verify_slot.acquire(blocking=False)
    try:
        busy = client.post(f"{API}/{D10.isoformat()}/verify")
        assert error_code(busy) == (409, "verification_running")
    finally:
        seals_router._verify_slot.release()
    root = layout.state / "mock-tsa" / "root.pem"
    root.rename(root.with_suffix(".bak"))
    no_roots = client.post(f"{API}/{D10.isoformat()}/verify")
    assert error_code(no_roots) == (409, "no_trusted_roots")
    detail = client.get(f"{API}/{D10.isoformat()}").json()
    assert detail["token"]["check"] == {
        "ok": None,
        "errors": ["no trusted root certificates"],
        "errors_tr": ["güvenilen kök sertifika yok (tsa.roots_dir)"],
        "root": None,
    }


# -- seal now and permissions ---------------------------------------------------------------------


def test_seal_now_is_not_available_through_the_api(
    client: TestClient, login_as: Login, vault: SealedVault
) -> None:
    login_as(client, "admin")
    response = client.post(f"{API}/{D14.isoformat()}/seal")
    assert error_code(response) == (501, "seal_now_unavailable")
    assert "\\ARCILOGGER\\Seal" in response.json()["error"]["message_tr"]
    assert "Task Scheduler" in response.json()["error"]["message_en"]


def test_permissions_per_role(app: Any, login_as: Login, vault: SealedVault) -> None:
    with TestClient(app) as auditor:
        login_as(auditor, "auditor")
        assert auditor.get(f"{API}/calendar").status_code == 200
        assert auditor.get(f"{API}/{D10.isoformat()}").status_code == 200
        assert error_code(auditor.post(f"{API}/{D14.isoformat()}/seal")) == (403, "forbidden")
    for role in ("hr", "viewer"):
        with TestClient(app) as other:
            login_as(other, role)
            for response in (
                other.get(f"{API}/calendar"),
                other.get(f"{API}/{D10.isoformat()}"),
                other.get(f"{API}/{D10.isoformat()}/manifest"),
                other.post(f"{API}/{D10.isoformat()}/verify"),
                other.post(f"{API}/{D10.isoformat()}/seal"),
            ):
                assert error_code(response) == (403, "forbidden")
    with TestClient(app) as anonymous:
        assert error_code(anonymous.get(f"{API}/calendar")) == (401, "not_authenticated")


# -- the view without HTTP ------------------------------------------------------------------------


def test_replica_missing_when_enabled_and_sealed_state_without_files(
    vault: SealedVault, layout: Layout, api_config: Config, tmp_path: Path
) -> None:
    config = Config.model_validate(
        {
            **api_config.model_dump(),
            "replica": {"enabled": True, "target": str(tmp_path / "nas")},
        }
    )
    view = VaultView(layout, config, now=datetime(2026, 10, 1, tzinfo=UTC))
    assert view.summary(D10)["replica"] == "missing"
    vault.unlock_all()
    layout.token(D11).unlink()
    gone = view.summary(D11)
    assert gone["state"] == "failed"
    assert "zaman damgası" in gone["message_tr"]


def test_a_failed_attempt_is_described_by_its_code_on_the_day_page(
    client: TestClient, login_as: Login, vault: SealedVault
) -> None:
    login_as(client, "auditor")
    detail = client.get(f"{API}/{D13.isoformat()}").json()
    state = detail["seal_state"]
    assert state["last_error"] == "mock TSA unreachable (injected)"  # technical detail, kept
    assert state["last_error_code"] == "tsa_unavailable"
    assert state["last_error_tr"] == "Zaman damgası sunucusuna ulaşılamadı."
    assert detail["message_tr"].startswith("Mühürleme başarısız: zaman damgası sunucusuna")
    assert "Kamu SM" not in detail["message_tr"]  # the mock TSA is not Kamu SM


def test_an_unreadable_spool_folder_never_breaks_the_calendar(
    client: TestClient,
    login_as: Login,
    vault: SealedVault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows: the API service may not list ``spool`` (folder rights). Days without a state file
    are then "erişilemiyor", never an HTTP 500."""

    def denied(self: Layout, day: date) -> list[tuple[str, Path]]:
        raise PermissionError(13, "Access is denied", str(self.spool))

    monkeypatch.setattr(Layout, "spool_files_for", denied)
    login_as(client, "admin")
    response = client.get(f"{API}/calendar", params={"month": "2026-09"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["problems"] == ["spool: PermissionError"]
    days = {date.fromisoformat(cell["day"]): cell for cell in body["days"]}
    unknown = days[date(2026, 9, 9)]
    assert unknown["state"] == "pending"
    assert "erişilemiyor" in unknown["message_tr"]
    assert days[D10]["state"] == "sealed"  # sealed days are read from the vault as before
    assert client.get(f"{API}/2026-09-09").status_code == 200


def test_a_skipped_empty_day_is_shown_as_no_data(
    layout: Layout, api_config: Config, tmp_path: Path
) -> None:
    """A day of a long run without any log (seal.max_consecutive_empty_days) is not pending."""
    state = layout.seal_state(date(2026, 9, 20))
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"day": "2026-09-20", "status": "skipped", "covered_by": "2026-09-25"}),
        encoding="utf-8",
    )
    view = VaultView(layout, api_config, now=datetime(2026, 10, 1, tzinfo=UTC))
    cell = view.summary(date(2026, 9, 20))
    assert cell["state"] == "no_data"
    assert "2026-09-25" in cell["message_tr"]


def test_a_claimed_production_stamp_is_neither_legal_grade_nor_a_test_stamp(
    vault: SealedVault, layout: Layout, api_config: Config
) -> None:
    """The day page gets the manifest's claim apart from the confirmed legal grade, so a Kamu
    SM production day whose root is not (yet) in tsa.legal_root_fingerprints is not shown as a
    test stamp (decision 5)."""
    manifest = json.loads(layout.manifest(D11).read_bytes())
    claimed = {**manifest, "tsa": {**manifest["tsa"], "provider": "zamane", "legal_grade": True}}
    roots = read_roots(layout, api_config.tsa.roots_dir, provider="mock")
    token = VaultView(layout, api_config)._token(D11, claimed, roots)
    assert token is not None
    assert token["check"]["ok"] is True
    assert (token["legal_grade"], token["legal_grade_claimed"]) == (False, True)
