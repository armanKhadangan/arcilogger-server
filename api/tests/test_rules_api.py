"""The rules API and the alert additions of Wave 6 (contract §2.6.1-§2.6.3, test T-A8):
permissions, the module lock, the audit ``rule.update`` with before and after, the dry run, the
assignment and the evidence. A test rule stands in for the rule
packages (W6-B…E), registered for the test only."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.routers import rules as rules_router
from arcilogger_api.testing import LoggedIn, make_user
from arcilogger_db.models import Alert, AlertEvent, AlertEvidence, AuditLog, Setting
from arcilogger_worker.rules import registry
from arcilogger_worker.rules.dryrun import DryRunTimeoutError
from arcilogger_worker.rules.model import Hit, Param, RuleContext, RuleDef

Login = Callable[..., LoggedIn]
API = "/api/v1"
NOW = datetime.now(UTC).replace(microsecond=0)


def _nothing(session: Session, context: RuleContext) -> Iterable[Hit]:
    return ()


RULE = RuleDef(
    id="R01",
    kind="event",
    severity="high",
    window_seconds=60,
    cooldown_seconds=0,
    params=(
        Param("threshold_ports", "int", 30, 1, 65535, "Port eşiği", "Port threshold"),
        Param("deny_actions", "strings", ("deny",), 1, 20, "Eylemler", "Actions"),
    ),
    inputs=("fg_traffic",),
    evaluate=_nothing,
)
PEOPLE = RuleDef(
    id="R09",
    kind="event",
    severity="low",
    window_seconds=3600,
    cooldown_seconds=0,
    params=(),
    inputs=("fg_web",),
    evaluate=_nothing,
    person_level=True,
)


@pytest.fixture(autouse=True)
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "RULES", (RULE, PEOPLE))
    monkeypatch.setattr(registry, "_BY_ID", {"R01": RULE, "R09": PEOPLE})


@pytest.fixture
def admin(client: TestClient, login_as: Login) -> LoggedIn:
    return login_as(client, "admin", username="yonetici")


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        session.expunge_all()
        return rows


def add_alert(factory: sessionmaker[Session], **fields: Any) -> int:
    values: dict[str, Any] = {
        "rule": "R01",
        "severity": "high",
        "state": "open",
        "title": "İç adres 172.20.41.17 47 farklı porta / 3 adrese engellendi",
        "details": {"title_en": "x", "evidence_total": 47, "key": "172.20.41.17"},
        "dedup_key": "R01:172.20.41.17",
        "time": NOW,
    }
    values.update(fields)
    with factory() as session:
        alert = Alert(**values)
        session.add(alert)
        session.commit()
        return alert.id


# -- GET /rules --------------------------------------------------------------------------------


def test_the_rule_list(client: TestClient, admin: LoggedIn) -> None:
    body = client.get(f"{API}/rules").json()
    assert body["module_enabled"] is True
    first = body["rules"][0]
    assert first["id"] == "R01"
    assert first["name_tr"] == "İçeriden port taraması"
    assert first["name_en"] == "Internal port scan"
    assert first["kind"] == "event"
    assert first["inputs"] == ["fg_traffic"]
    assert (first["person_level"], first["format_verified"], first["needs_setup"]) == (
        False,
        True,
        False,
    )
    assert first["data_status"] == "never"
    assert first["defaults"] == first["settings"]
    assert first["defaults"]["actions"] == {"email": True, "webhook": True}
    assert first["defaults"]["params"] == {"threshold_ports": 30, "deny_actions": ["deny"]}
    assert first["params"][0] == {
        "key": "threshold_ports",
        "kind": "int",
        "min": 1,
        "max": 65535,
        "label_tr": "Port eşiği",
        "label_en": "Port threshold",
    }
    assert first["advice_tr"].endswith(".")
    assert first["note_tr"] == "Başlangıç değeri; ağınıza göre ayarlayın."
    assert [rule["id"] for rule in body["rules"]] == ["R01", "R09"]


@pytest.mark.parametrize("role", ["auditor", "hr", "viewer"])
def test_only_administrators_see_and_change_rules(
    client: TestClient, login_as: Login, role: str
) -> None:
    login_as(client, role)
    assert error(client.get(f"{API}/rules")) == (403, "forbidden")
    assert error(client.put(f"{API}/rules/R01", json={})) == (403, "forbidden")
    assert error(client.post(f"{API}/rules/R01/dry-run", json={})) == (403, "forbidden")


# -- PUT /rules/{id} -----------------------------------------------------------------------------


def test_saving_stores_only_the_differences_and_audits_before_and_after(
    client: TestClient, admin: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    body = {
        "enabled": True,  # the default: not stored
        "severity": "critical",
        "params": {"threshold_ports": 30, "deny_actions": ["deny", "drop"]},
        "actions": {"email": True, "webhook": False},
    }
    response = client.put(f"{API}/rules/R01", json=body)
    assert response.status_code == 200, response.text
    settings = response.json()["settings"]
    assert settings["severity"] == "critical"
    assert settings["params"]["deny_actions"] == ["deny", "drop"]
    assert settings["actions"] == {"email": True, "webhook": False}
    with app_session_factory() as session:
        stored = session.get(Setting, "rules.R01")
        assert stored is not None
        assert stored.value == {
            "severity": "critical",
            "params": {"deny_actions": ["deny", "drop"]},
            "actions": {"webhook": False},
        }
        assert stored.updated_by == "yonetici"
    (entry,) = audits(app_session_factory, "rule.update")
    assert entry.details["rule"] == "R01"
    assert entry.details["before"]["severity"] == "high"
    assert entry.details["after"]["severity"] == "critical"
    assert entry.details["after"]["params"]["deny_actions"] == ["deny", "drop"]
    assert entry.target == "rule:R01"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"severity": "urgent"}, "severity"),
        ({"params": {"threshold_ports": 0}}, "params.threshold_ports"),
        ({"params": {"deny_actions": ["x" * 201]}}, "params.deny_actions"),
        ({"colour": "red"}, "colour"),
    ],
)
def test_an_invalid_setting_names_its_field(
    client: TestClient, admin: LoggedIn, body: dict[str, Any], field: str
) -> None:
    response = client.put(f"{API}/rules/R01", json=body)
    assert error(response) == (422, "rule_param_invalid")
    payload = response.json()["error"]
    assert payload["details"] == {"field": field}
    assert payload["message_tr"] == f"Kural ayarı geçersiz: {field}."
    assert payload["message_en"] == f"Invalid rule setting: {field}."


def test_an_unknown_rule(client: TestClient, admin: LoggedIn) -> None:
    for rule_id in ("R15", "R02"):  # R15 is no security rule, R02 is not registered here
        response = client.put(f"{API}/rules/{rule_id}", json={})
        assert error(response) == (404, "rule_not_found")
        assert response.json()["error"]["message_tr"] == "Böyle bir güvenlik kuralı yok."
    assert client.put(f"{API}/rules/r01", json={}).status_code == 422


# -- dry run -------------------------------------------------------------------------------------


def test_the_dry_run(client: TestClient, admin: LoggedIn, monkeypatch: pytest.MonkeyPatch) -> None:
    response = client.post(f"{API}/rules/R01/dry-run", json={"params": {"threshold_ports": 5}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["would_open"], body["keys"], body["truncated"]) == (0, [], False)
    assert error(client.post(f"{API}/rules/R01/dry-run", json={"severity": "x"})) == (
        422,
        "rule_param_invalid",
    )
    assert rules_router._DRY_RUN.acquire(blocking=False)
    try:
        assert error(client.post(f"{API}/rules/R01/dry-run", json={})) == (
            409,
            "rule_dry_run_busy",
        )
    finally:
        rules_router._DRY_RUN.release()

    def too_long(*args: Any) -> None:
        raise DryRunTimeoutError(30)

    monkeypatch.setattr(rules_router, "dry_run", too_long)
    response = client.post(f"{API}/rules/R01/dry-run", json={})
    assert error(response) == (504, "rule_dry_run_timeout")
    assert response.json()["error"]["message_tr"].startswith("Deneme 30 sn içinde bitmedi")


# -- assignment ----------------------------------------------------------------------------------


def test_assignment(
    client: TestClient, admin: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    alert_id = add_alert(app_session_factory)
    other = make_user(app_session_factory, "admin", username="guvenlik")
    hr = make_user(app_session_factory, "hr", username="ik")
    response = client.post(f"{API}/alerts/{alert_id}/assign", json={"user_id": other.id})
    assert response.status_code == 200, response.text
    assert response.json()["assignee"] == {"id": other.id, "username": "guvenlik"}
    events = response.json()["events"]
    assert events[-1]["kind"] == "assign"
    assert events[-1]["text"] == "guvenlik"
    # The same assignee again records nothing; nobody clears it.
    client.post(f"{API}/alerts/{alert_id}/assign", json={"user_id": other.id})
    response = client.post(f"{API}/alerts/{alert_id}/assign", json={"user_id": None})
    assert response.json()["assignee"] is None
    assert [event["text"] for event in response.json()["events"]] == ["guvenlik", None]
    entries = audits(app_session_factory, "alert.assign")
    assert [entry.details["to"] for entry in entries] == [other.id, None]
    # Only users who may read alerts.
    for user_id in (hr.id, 999_999):
        response = client.post(f"{API}/alerts/{alert_id}/assign", json={"user_id": user_id})
        assert error(response) == (422, "alert_assignee_invalid")
        assert response.json()["error"]["message_tr"] == "Bu kullanıcıya uyarı atanamaz."


def test_the_assignee_filter(
    client: TestClient, admin: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    mine = add_alert(app_session_factory, assignee_id=admin.user_id)
    other_user = make_user(app_session_factory, "admin", username="guvenlik")
    theirs = add_alert(app_session_factory, dedup_key="R01:b", assignee_id=other_user.id)
    nobody = add_alert(app_session_factory, dedup_key="R01:c")

    def ids(query: str) -> list[int]:
        return sorted(item["id"] for item in client.get(f"{API}/alerts?{query}").json()["items"])

    assert ids("assignee=me") == [mine]
    assert ids("assignee=none") == [nobody]
    assert ids(f"assignee={other_user.id}") == [theirs]
    assert client.get(f"{API}/alerts?assignee=someone").status_code == 422


# -- evidence ------------------------------------------------------------------------------------


def test_the_evidence_of_an_alert(
    client: TestClient, admin: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    alert_id = add_alert(app_session_factory)
    with app_session_factory() as session:
        session.add_all(
            [
                AlertEvidence(
                    alert_id=alert_id,
                    n=1,
                    tbl="fg_traffic",
                    row_id=41,
                    time=NOW - timedelta(seconds=2),
                    time_received=NOW - timedelta(seconds=1),
                    source="fw1",
                    raw_day=date(2026, 11, 12),
                    raw_line=17,
                    raw_offset=4096,
                ),
                AlertEvidence(alert_id=alert_id, n=2, tbl="devices", time=NOW),
            ]
        )
        session.commit()
    body = client.get(f"{API}/alerts/{alert_id}/evidence").json()
    assert body["alert_id"] == alert_id
    assert body["evidence_total"] == 47
    first, device = body["items"]
    assert first["table"] == "fg_traffic"
    assert first["raw"] == {"source": "fw1", "day": "2026-11-12", "line": 17, "offset": 4096}
    assert device["raw"] is None
    assert "line" not in str(device)
    listed = client.get(f"{API}/alerts").json()["items"][0]
    assert listed["evidence_total"] == 47
    assert error(client.get(f"{API}/alerts/999999/evidence")) == (404, "alert_not_found")


def test_an_assign_event_has_no_state(app_session_factory: sessionmaker[Session]) -> None:
    alert_id = add_alert(app_session_factory)
    with app_session_factory() as session:
        session.add(AlertEvent(alert_id=alert_id, kind="assign", text=None, actor="yonetici"))
        session.commit()
        events = list(session.scalars(select(AlertEvent).where(AlertEvent.alert_id == alert_id)))
        assert [(event.kind, event.from_state) for event in events] == [("assign", None)]


def test_the_generic_settings_list_the_rules_apart(client: TestClient, admin: LoggedIn) -> None:
    """Deviation W6-A-3: ``GET /settings`` answers the ``rules.*`` keys in ``elsewhere`` (for
    backups), never in a group of the generic page, and ``PATCH`` refuses them."""
    body = client.get(f"{API}/settings").json()
    in_groups = {item["key"] for group in body["groups"] for item in group["settings"]}
    assert not {key for key in in_groups if key.startswith("rules.")}
    elsewhere = {item["key"]: item for item in body["elsewhere"]}
    assert elsewhere["rules.lag_seconds"]["value"] == 120
    assert elsewhere["rules.R01"]["is_default"] is True
    client.put(f"{API}/rules/R01", json={"severity": "low"})
    body = client.get(f"{API}/settings").json()
    changed = next(item for item in body["elsewhere"] if item["key"] == "rules.R01")
    assert changed["value"] == {"severity": "low"}
    assert changed["updated_by"] == "yonetici"
    response = client.patch(
        f"{API}/settings",
        json={"changes": [{"key": "rules.R01", "old": {"severity": "low"}, "value": {}}]},
    )
    assert response.status_code == 422
