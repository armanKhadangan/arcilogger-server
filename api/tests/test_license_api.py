"""``/api/v1/license`` (licensing contract 6.1-6.3; tests B-T6, B-T7, B-T8, B-T11, B-T13, B-T33).

Real evaluation (``license_live``): the deterministic test key is trusted, the server id is the
default test id (``XW68-7K1Z-5FGV-AGT8``), licence files are signed by the test helpers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

import arcilogger_core
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.license import errors, fingerprint, keys, revoked
from arcilogger_core.license.testing import (
    fixed_identity,
    make_license,
    tamper,
    test_private_key,
)
from arcilogger_core.timeutil import utc_now
from arcilogger_db.audit import append_entry
from arcilogger_db.seed import seed
from arcilogger_worker.vaulttesting import at_site, make_vault

pytestmark = pytest.mark.license_live

Login = Callable[..., LoggedIn]
URL = "/api/v1/license"
GATED = "/api/v1/usage/company"
ALL = ["core_5651", "usage_analytics", "usage_reports", "bulk_import"]


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def enabled(document: dict[str, Any]) -> list[str]:
    return [item["key"] for item in document["modules"] if item["enabled"]]


def state_of(client: TestClient) -> dict[str, Any]:
    response = client.get(URL)
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    return document


# -- B-T6 GET /license ----------------------------------------------------------------------------


def test_get_license_needs_a_session(client: TestClient, licence: Any) -> None:
    client.get("/api/v1/health")
    assert error(client.get(URL)) == (401, "not_authenticated")


def test_trial_document_for_admin_and_other_roles(
    app: Any, client: TestClient, login_as: Login, licence: Any
) -> None:
    licence.admin(client, login_as)
    document = state_of(client)
    assert document["status"] == "trial"
    assert document["problem"] is None
    assert enabled(document) == ALL
    assert [item["licensed"] for item in document["modules"]] == [False] * 4
    assert document["modules"][0]["always"] is True
    assert document["trial"]["days_left"] == 14
    assert document["licensee"] is None
    assert document["maintenance"] is None
    assert document["limits"]["users"] == {
        "max": None,
        "used": 1,
        "unconfigured": 0,
        "exceeded": False,
        "full": False,
    }
    assert document["product"]["name"] == "ARCILOGGER"
    assert document["licensor"] == {"name": "Arman Khadangan", "email": "arman@khadangan.com"}
    details = document["details"]
    assert details["server_id"] == licence.server_id
    assert details["license_id"] is None
    assert details["request_code"].startswith("ARCIREQ1-")
    assert details["organization"] == "Örnek A.Ş."
    assert details["time"]["source"] in ("system", "release_date")
    assert details["history"] == []
    for role in ("viewer", "hr", "auditor"):
        other = TestClient(app, base_url="http://testserver")
        login_as(other, role)
        response = other.get(URL)
        assert response.status_code == 200, role
        assert response.json()["details"] is None, role
        assert response.json()["status"] == "trial"


def test_every_state_through_get_license(
    client: TestClient, login_as: Login, licence: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    licence.admin(client, login_as)
    licence.trial_over()
    over = state_of(client)
    assert (over["status"], over["problem"]) == ("trial_expired", None)
    assert enabled(over) == ["core_5651"]
    assert over["limits"]["users"]["max"] == 3
    assert over["trial"]["days_left"] == 0

    data = make_license(
        server_ids=[licence.server_id], modules=("core_5651", "usage_analytics"), users=25
    )
    assert licence.upload(client, data).status_code == 200
    licensed = state_of(client)
    assert (licensed["status"], licensed["licensee"]) == ("licensed", "Örnek A.Ş.")
    assert enabled(licensed) == ["core_5651", "usage_analytics"]
    assert licensed["limits"]["users"]["max"] == 25
    assert licensed["trial"] is None
    assert licensed["maintenance"]["covers_version"] is True
    assert licensed["details"]["license_id"] == "ARCI-L-20261001-TEST0001"

    # invalid: the key is no longer trusted (an update removed it)
    with monkeypatch.context() as patched:
        patched.setattr(keys, "TRUSTED_KEYS", {})
        licence.invalidate()
        invalid = state_of(client)
        assert (invalid["status"], invalid["problem"]) == ("invalid", "license_unknown_key")
        assert invalid["licensee"] is None
        assert invalid["limits"]["users"]["max"] == 3
        assert invalid["details"]["license_id"] == "ARCI-L-20261001-TEST0001"  # read, not trusted
    # revoked
    with monkeypatch.context() as patched:
        patched.setattr(revoked, "REVOKED_LICENSE_IDS", frozenset({"ARCI-L-20261001-TEST0001"}))
        licence.invalidate()
        assert (state_of(client)["status"], state_of(client)["problem"]) == (
            "invalid",
            "license_revoked",
        )
    # wrong server: another machine, and an unreadable server id
    with fixed_identity(machine_id="11111111-2222-4333-8444-555555555555"):
        licence.invalidate()
        other = state_of(client)
        assert (other["status"], other["problem"]) == ("wrong_server", "license_other_server")
        assert other["limits"]["users"]["max"] == 25  # the document's limits stay
        assert enabled(other) == ["core_5651"]

    def unreadable() -> Any:
        raise fingerprint.FingerprintError("no SMBIOS table")

    with monkeypatch.context() as patched:
        patched.setattr(fingerprint, "IDENTITY_PROVIDER", unreadable)
        fingerprint.clear_cache()
        licence.invalidate()
        lost = state_of(client)
        assert (lost["status"], lost["problem"]) == (
            "wrong_server",
            "license_server_id_unavailable",
        )
        assert lost["details"]["server_id"] is None
        assert lost["details"]["request_code"] is None
    fingerprint.clear_cache()
    # expired and version_not_covered (stored when they still applied)
    yesterday = utc_now().date() - timedelta(days=2)
    licence.store(
        make_license(server_ids=[licence.server_id], valid_until=yesterday, license_id=new_id(2))
    )
    expired = state_of(client)
    assert (expired["status"], expired["problem"]) == ("expired", "license_expired")
    assert expired["valid_until"] == yesterday.isoformat()
    licence.store(
        make_license(
            server_ids=[licence.server_id],
            maintenance_until=date(2026, 1, 1),
            license_id=new_id(3),
        )
    )
    old = state_of(client)
    assert (old["status"], old["problem"]) == ("version_not_covered", "license_version_not_covered")
    assert old["maintenance"]["covers_version"] is False
    assert old["maintenance"]["expired"] is True


def new_id(number: int) -> str:
    return f"ARCI-L-20261001-TEST000{number}"


def test_the_shown_licence_comes_from_the_file_not_the_index_columns(
    client: TestClient,
    login_as: Login,
    licence: Any,
    app_session_factory: sessionmaker[Session],
) -> None:
    licence.admin(client, login_as)
    assert licence.upload(client, make_license(server_ids=[licence.server_id])).status_code == 200
    with app_session_factory() as session:
        session.execute(text("ALTER TABLE license_files DISABLE TRIGGER USER"))
        session.execute(
            text(
                "UPDATE license_files SET licensee = 'Sahte A.Ş.', "
                "license_id = 'ARCI-L-20990101-FAKE0000', key_id = 'arci-fake'"
            )
        )
        session.execute(text("ALTER TABLE license_files ENABLE TRIGGER USER"))
        session.commit()
    licence.invalidate()
    document = state_of(client)
    assert document["licensee"] == "Örnek A.Ş."
    assert document["details"]["license_id"] == "ARCI-L-20261001-TEST0001"
    assert document["details"]["key_id"] == "test-key-1"
    assert "Sahte" not in json.dumps(document, ensure_ascii=False)


# -- B-T7 POST /license ---------------------------------------------------------------------------


def test_upload_success_and_unchanged_reupload(
    client: TestClient, login_as: Login, licence: Any
) -> None:
    who = licence.admin(client, login_as)
    data = make_license(server_ids=[licence.server_id], modules=("core_5651", "usage_reports"))
    response = licence.upload(client, data)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["status"] == "licensed"
    assert document["details"]["installed_by"] == who.username
    # usage_reports without usage_analytics stays off
    assert enabled(document) == ["core_5651"]
    reformatted = json.dumps(json.loads(data), indent=1, sort_keys=True).encode()
    for again in (data, reformatted):
        assert licence.upload(client, again).status_code == 200
    installs = licence.events("install")
    assert [event.details["unchanged"] for event in installs] == [False, True, True]
    assert installs[0].source == "web"
    assert installs[0].actor == who.username
    (first, *_) = licence.audits("license.install")
    assert first.target == "license:ARCI-L-20261001-TEST0001"
    assert first.actor_username == who.username
    assert "licensee" not in first.details
    assert "server_ids" not in first.details
    assert first.details["sha256"] == hashlib.sha256(data).hexdigest()


def _unknown_key_licence(server_id: str) -> bytes:
    return make_license(
        server_ids=[server_id], key_id="arci-other", private_key=test_private_key(b"other")
    )


REFUSALS: list[tuple[str, int, str]] = [
    ("malformed", 422, "license_malformed"),
    ("unknown_key", 422, "license_unknown_key"),
    ("signature", 422, "license_signature_invalid"),
    ("revoked", 422, "license_revoked"),
    ("other_server", 409, "license_other_server"),
    ("expired", 409, "license_expired"),
    ("not_covered", 409, "license_version_not_covered"),
    ("server_id", 409, "license_server_id_unavailable"),
    ("file_too_large", 413, "license_too_large"),
]


@pytest.mark.parametrize(("case", "status", "code"), REFUSALS)
def test_each_refusal_is_answered_recorded_and_audited(
    client: TestClient,
    login_as: Login,
    licence: Any,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    status: int,
    code: str,
) -> None:
    who = licence.admin(client, login_as)
    server_id = licence.server_id
    good = make_license(server_ids=[server_id])
    details: dict[str, Any] | None = None
    data = good
    if case == "malformed":
        data = b"kein json"
    elif case == "unknown_key":
        data = _unknown_key_licence(server_id)
        details = {"key_id": "arci-other"}
    elif case == "signature":
        data = tamper(good, "users", 999)
    elif case == "revoked":
        monkeypatch.setattr(revoked, "REVOKED_LICENSE_IDS", frozenset({"ARCI-L-20261001-TEST0001"}))
        details = {"license_id": "ARCI-L-20261001-TEST0001"}
    elif case == "other_server":
        data = make_license(server_ids=["AAAA-BBBB-CCCC-DDDD"])
        details = {"server_id": server_id, "allowed": ["AAAA-BBBB-CCCC-DDDD"]}
    elif case == "expired":
        data = make_license(server_ids=[server_id], valid_until=date(2026, 10, 2))
        details = {"valid_until": "2026-10-02"}
    elif case == "not_covered":
        data = make_license(server_ids=[server_id], maintenance_until=date(2026, 1, 31))
        details = {
            "version": arcilogger_core.__version__,
            "release_date": arcilogger_core.RELEASE_DATE,
            "maintenance_until": "2026-01-31",
        }
    elif case == "server_id":

        def unreadable() -> Any:
            raise OSError("registry unreadable")

        monkeypatch.setattr(fingerprint, "IDENTITY_PROVIDER", unreadable)
        fingerprint.clear_cache()
    elif case == "file_too_large":
        data = good + b" " * (70_000 - len(good))  # a 70,000-byte part inside a smaller body
    response = licence.upload(client, data)
    fingerprint.clear_cache()
    assert error(response) == (status, code)
    body = response.json()["error"]
    text_tr, text_en = errors.MESSAGES[code]
    if "{" not in text_tr:
        assert (body["message_tr"], body["message_en"]) == (text_tr, text_en)
    else:
        assert "{" not in body["message_tr"]
        assert "{" not in body["message_en"]
    assert body.get("details") == details
    (event,) = licence.events("refuse")  # committed although the request failed
    assert event.source == "web"
    assert event.actor == who.username
    assert event.details["code"] == code
    assert event.details["sha256"] == hashlib.sha256(data).hexdigest()
    assert event.details["size"] == len(data)
    (entry,) = licence.audits("license.refuse")
    assert entry.details == event.details
    assert entry.target == "license"
    assert licence.events("install") == []


def test_a_body_too_large_to_read_is_refused_and_recorded(
    client: TestClient, login_as: Login, licence: Any
) -> None:
    licence.admin(client, login_as)
    response = licence.upload(client, b"x" * 140_000)
    assert error(response) == (413, "license_too_large")
    assert response.json()["error"]["message_tr"] == errors.MESSAGES["license_too_large"][0]
    (event,) = licence.events("refuse")
    assert event.details["sha256"] is None
    declared = int(response.request.headers["content-length"])
    assert declared > 131_072
    assert event.details["size"] == declared  # the declared Content-Length
    assert event.details["code"] == "license_too_large"
    assert len(licence.audits("license.refuse")) == 1


def test_upload_needs_a_recent_sign_in_and_settings_manage(
    app: Any, client: TestClient, login_as: Login, licence: Any
) -> None:
    data = make_license(server_ids=[licence.server_id])
    login_as(client, "admin")  # no recent re-authentication
    assert error(licence.upload(client, data)) == (403, "reauth_required")
    viewer = TestClient(app, base_url="http://testserver")
    who = login_as(viewer, "viewer")
    licence.recent(who)
    assert error(licence.upload(viewer, data)) == (403, "forbidden")
    assert error(viewer.delete(URL)) == (403, "forbidden")
    assert licence.events() == []


def test_multipart_without_the_file_part(client: TestClient, login_as: Login, licence: Any) -> None:
    licence.admin(client, login_as)
    response = client.post(URL, files={"other": ("x.lic", b"{}", "application/json")})
    assert error(response) == (422, "upload_invalid")
    assert licence.events() == []
    assert licence.audits("license.refuse") == []


# -- B-T8 DELETE /license and B-T13 the cache -----------------------------------------------------


def test_delete_and_the_cache(client: TestClient, login_as: Login, licence: Any) -> None:
    licence.admin(client, login_as)
    licence.trial_over()
    assert error(client.get(GATED)) == (403, "module_not_licensed")
    data = make_license(server_ids=[licence.server_id], modules=("core_5651", "usage_analytics"))
    assert licence.upload(client, data).status_code == 200
    assert client.get(GATED).status_code == 200  # the next request, no cache delay
    response = client.delete(URL)
    assert response.status_code == 204
    assert error(client.get(GATED)) == (403, "module_not_licensed")
    assert state_of(client)["status"] == "trial_expired"
    assert error(client.delete(URL)) == (404, "license_not_installed")
    (removed,) = licence.events("remove")
    assert removed.details["license_id"] == "ARCI-L-20261001-TEST0001"
    (entry,) = licence.audits("license.remove")
    assert entry.target == "license:ARCI-L-20261001-TEST0001"


def test_delete_within_the_trial_returns_to_the_trial(
    client: TestClient, login_as: Login, licence: Any
) -> None:
    licence.admin(client, login_as)
    assert licence.upload(client, make_license(server_ids=[licence.server_id])).status_code == 200
    assert state_of(client)["status"] == "licensed"
    assert client.delete(URL).status_code == 204
    assert state_of(client)["status"] == "trial"


# -- B-T33 run-time enforcement of maintenance ----------------------------------------------------


def test_a_later_release_after_the_maintenance_locks_the_paid_modules(
    client: TestClient, login_as: Login, licence: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    licence.admin(client, login_as)
    licence.trial_over()
    data = make_license(
        server_ids=[licence.server_id],
        modules=("core_5651", "usage_analytics"),
        maintenance_until=date(2026, 12, 31),
    )
    assert licence.upload(client, data).status_code == 200
    assert client.get(GATED).status_code == 200
    monkeypatch.setattr(arcilogger_core, "RELEASE_DATE", "2027-02-01")  # the upgrade
    licence.invalidate()
    document = state_of(client)
    assert (document["status"], document["problem"]) == (
        "version_not_covered",
        "license_version_not_covered",
    )
    assert document["product"]["release_date"] == "2027-02-01"
    assert error(client.get(GATED)) == (403, "module_not_licensed")
    assert client.get("/api/v1/dashboard/summary").status_code == 200


# -- B-T11 trial reset attempts -------------------------------------------------------------------


def test_a_fresh_database_with_an_old_data_folder(
    client: TestClient, login_as: Login, licence: Any, api_config: Config
) -> None:
    today = utc_now().date()
    days = [today - timedelta(days=21), today - timedelta(days=20)]
    vault = make_vault(api_config.data_dir, tsa_now=at_site(days[0] - timedelta(days=1)))
    try:
        vault.seal_days(days)
        licence.invalidate()
        licence.admin(client, login_as)
        document = state_of(client)
        assert (document["status"], document["problem"]) == ("trial_expired", None)
        assert error(client.get(GATED)) == (403, "module_not_licensed")
    finally:
        vault.unlock_all()


def test_a_planted_pre_epoch_vault_day_does_not_reopen_the_trial(
    client: TestClient, login_as: Login, licence: Any, api_config: Config
) -> None:
    """A local administrator owns the data folder: one empty, correctly named manifest from
    before PRODUCT_EPOCH must not hide the real oldest sealed day (3.4, A-T12/B-T11)."""
    today = utc_now().date()
    days = [today - timedelta(days=21), today - timedelta(days=20)]
    vault = make_vault(api_config.data_dir, tsa_now=at_site(days[0] - timedelta(days=1)))
    try:
        vault.seal_days(days)
        planted = api_config.data_dir / "vault" / "2025" / "12" / "31" / "manifest-2025-12-31.json"
        planted.parent.mkdir(parents=True)
        planted.write_text("{}", encoding="utf-8")
        licence.invalidate()
        licence.admin(client, login_as)
        document = state_of(client)
        assert (document["status"], document["problem"]) == ("trial_expired", None)
        assert document["trial"]["days_left"] == 0
        assert error(client.get(GATED)) == (403, "module_not_licensed")
    finally:
        vault.unlock_all()


def test_a_vault_day_without_an_end_does_not_break_the_licence_page(
    client: TestClient, login_as: Login, licence: Any, api_config: Config
) -> None:
    """``manifest-9999-12-31.json``: trusted_now never raises (3.3), so GET /license answers."""
    planted = api_config.data_dir / "vault" / "9999" / "12" / "31" / "manifest-9999-12-31.json"
    planted.parent.mkdir(parents=True)
    planted.write_text("{}", encoding="utf-8")
    licence.invalidate()
    licence.admin(client, login_as)
    response = client.get("/api/v1/license")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "trial"


def test_a_deleted_installation_row_comes_back_backdated(
    client: TestClient,
    login_as: Login,
    licence: Any,
    app_session_factory: sessionmaker[Session],
) -> None:
    licence.admin(client, login_as)
    assert state_of(client)["status"] == "trial"  # the row is created now
    old = utc_now() - timedelta(days=20)
    with app_session_factory() as session:
        append_entry(session, actor_id=None, actor="system", action="seal.run", clock=lambda: old)
        session.execute(text("ALTER TABLE license_installation DISABLE TRIGGER USER"))
        session.execute(text("DELETE FROM license_installation"))
        session.execute(text("ALTER TABLE license_installation ENABLE TRIGGER USER"))
        session.commit()
    licence.invalidate()
    document = state_of(client)
    assert document["status"] == "trial_expired"
    first, second = licence.audits("license.trial_start")
    assert (first.details["recreated"], first.details["backdated"]) == (False, False)
    assert (second.details["recreated"], second.details["backdated"]) == (True, True)
    assert datetime.fromisoformat(second.details["trial_started_at"]) == old
    assert error(client.get(GATED)) == (403, "module_not_licensed")


def test_a_seed_under_a_clock_years_ahead(
    client: TestClient,
    login_as: Login,
    licence: Any,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ahead = datetime.now(UTC) + timedelta(days=3 * 365)
    monkeypatch.setattr("arcilogger_db.seed.utc_now", lambda: ahead)
    with app_session_factory() as session:
        seed(session)
        session.commit()
    monkeypatch.undo()  # the clock back
    licence.invalidate()
    licence.admin(client, login_as)
    document = state_of(client)
    assert (document["status"], document["problem"]) == (
        "trial_expired",
        "trial_clock_inconsistent",
    )
    assert document["trial"]["days_left"] == 0
    assert error(client.get(GATED)) == (403, "module_not_licensed")
