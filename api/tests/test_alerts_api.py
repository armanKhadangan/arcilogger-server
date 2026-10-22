"""Alerts API (spec 11, 12.3 page 8; ADR-0010 amendment): permissions per role, list filters,
the detail (sanitised details, runbook, masked recipients), every state transition, notes, the
audit entries (never a note's text), system events and the summary's numbers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import product
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.routers.alerts import mask_recipient, sanitize
from arcilogger_api.security import ROLE_PERMISSIONS
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout
from arcilogger_db.models import ALERT_STATES, Alert, AlertDelivery, AlertEvent, AuditLog
from arcilogger_worker.alerts import store
from arcilogger_worker.alerts.store import TRANSITIONS, resolve

Login = Callable[..., LoggedIn]
API = "/api/v1/alerts"
NOW = datetime.now(UTC).replace(microsecond=0)
#: A note text that must never reach the audit log.
SECRET_NOTE = "Elif Kaya'nın bilgisayarı fişten çekilmiş (iz-7Q4K)"


@pytest.fixture
def factory(app_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return app_session_factory


@pytest.fixture
def admin(client: TestClient, login_as: Login) -> LoggedIn:
    return login_as(client, "admin", username="yonetici")


def add_alert(
    factory: sessionmaker[Session],
    *,
    rule: str = "R15",
    severity: str = "high",
    state: str = "open",
    title: str = "Log kaynağı sessiz: fortigate",
    details: dict[str, Any] | None = None,
    key: str | None = None,
    time: datetime | None = None,
    created: datetime | None = None,
    resolved_at: datetime | None = None,
) -> int:
    with factory() as session:
        alert = Alert(
            rule=rule,
            severity=severity,
            state=state,
            title=title,
            details=details if details is not None else {"source": "fortigate"},
            dedup_key=key,
            time=time or NOW,
            created_at=created or time or NOW,
            resolved_at=resolved_at,
        )
        session.add(alert)
        session.commit()
        return alert.id


def add_event(
    factory: sessionmaker[Session], alert_id: int, to_state: str, at: datetime, **fields: Any
) -> None:
    with factory() as session:
        session.add(
            AlertEvent(
                alert_id=alert_id,
                kind="state",
                from_state=fields.pop("from_state", "open"),
                to_state=to_state,
                actor=fields.pop("actor", "system"),
                at=at,
                **fields,
            )
        )
        session.commit()


def audits(factory: sessionmaker[Session], prefix: str = "alert.") -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(
                select(AuditLog).where(AuditLog.action.startswith(prefix)).order_by(AuditLog.id)
            )
        )
        for row in rows:
            session.expunge(row)
    return rows


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


# -- routes and roles -----------------------------------------------------------------------------

#: Every route of the area and the permission it needs.
RULES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/alerts"): "alerts.read",
    ("GET", "/api/v1/alerts/summary"): "alerts.read",
    ("GET", "/api/v1/alerts/{alert_id}"): "alerts.read",
    ("POST", "/api/v1/alerts/{alert_id}/state"): "alerts.manage",
    ("POST", "/api/v1/alerts/{alert_id}/notes"): "alerts.manage",
    ("GET", "/api/v1/dashboard/summary"): "dashboard.view",
}
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/v1/alerts/{alert_id}/state"): {"state": "acknowledged"},
    ("POST", "/api/v1/alerts/{alert_id}/notes"): {"text": "Bakıyorum."},
}


def test_every_route_is_classified(app: FastAPI) -> None:
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    found = sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(("/api/v1/alerts", "/api/v1/dashboard"))
        for method in operations
    )
    assert found == sorted(RULES)


@pytest.mark.parametrize("role", ["admin", "auditor", "hr", "viewer"])
def test_every_role_gets_exactly_its_permissions(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str
) -> None:
    login_as(client, role)
    for (method, path), permission in RULES.items():
        alert_id = add_alert(factory, key=f"R15:{role}:{path}")
        url = path.replace("{alert_id}", str(alert_id))
        response = client.request(method, url, json=BODIES.get((method, path)))
        if permission in ROLE_PERMISSIONS[role]:
            assert response.status_code in (200, 201), (role, method, path, response.text)
        else:
            assert error(response) == (403, "forbidden"), (role, method, path)


def test_alerts_are_for_administrators_only_today() -> None:
    holders = sorted(
        role for role, permissions in ROLE_PERMISSIONS.items() if "alerts.read" in permissions
    )
    assert holders == ["admin"]


def test_no_session_is_401(client: TestClient, factory: sessionmaker[Session]) -> None:
    alert_id = add_alert(factory)
    for method, path in RULES:
        url = path.replace("{alert_id}", str(alert_id))
        response = client.request(method, url, json=BODIES.get((method, path)))
        # Unsafe methods without a CSRF cookie are refused by the middleware first.
        assert response.status_code in (401, 403), (method, path)
        if method == "GET":
            assert error(response) == (401, "not_authenticated")


# -- list -----------------------------------------------------------------------------------------


@pytest.fixture
def several(factory: sessionmaker[Session]) -> dict[str, int]:
    """Six alerts of different rules, severities, states and times."""
    return {
        "r15": add_alert(factory, key="R15:fortigate", time=NOW - timedelta(minutes=5)),
        "disk": add_alert(
            factory,
            rule="R20-disk",
            severity="critical",
            title="Disk dolmak üzere: D: %96 dolu (eşik %80)",
            details={"drive": "D:", "used_percent": 96, "threshold_percent": 80},
            key="R20-disk:D:",
            time=NOW - timedelta(hours=1),
        ),
        "router": add_alert(
            factory,
            state="acknowledged",
            title="Log kaynağı sessiz: core-r1",
            details={"source": "core-r1", "threshold_minutes": 15},
            key="R15:core-r1",
            time=NOW - timedelta(hours=2),
        ),
        "conflict": add_alert(
            factory,
            rule="ip_conflict",
            severity="medium",
            state="resolved",
            title="IP çakışması: 172.20.43.120 Işık-PC ile",
            details={"ip": "172.20.43.120", "auto_resolved": {"reason": "lease_rebuild"}},
            time=NOW - timedelta(days=1),
            resolved_at=NOW - timedelta(hours=20),
        ),
        "credit": add_alert(
            factory,
            rule="R20-credit",
            severity="low",
            state="dismissed",
            title="Kamu SM kontörü azaldı",
            time=NOW - timedelta(days=2),
            details={},
        ),
        "info": add_alert(
            factory,
            rule="R20-retention",
            severity="info",
            title="İmha tamamlandı",
            time=NOW - timedelta(days=3),
            details={},
        ),
    }


def ids(response: Any) -> list[int]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_list_is_newest_first_with_total_and_pages(
    client: TestClient, admin: LoggedIn, several: dict[str, int]
) -> None:
    order = ["r15", "disk", "router", "conflict", "credit", "info"]
    assert ids(client.get(API)) == [several[name] for name in order]
    page = client.get(API, params={"limit": 2, "offset": 2}).json()
    assert (page["total"], page["limit"], page["offset"]) == (6, 2, 2)
    assert [item["id"] for item in page["items"]] == [several["router"], several["conflict"]]
    assert ids(client.get(API, params={"order": "asc"})) == [several[n] for n in reversed(order)]
    by_severity = ids(client.get(API, params={"sort": "severity"}))
    assert by_severity[:2] == [several["disk"], several["r15"]]
    assert by_severity[-1] == several["info"]
    by_state = ids(client.get(API, params={"sort": "state", "order": "asc"}))
    assert by_state[-2:] == [several["conflict"], several["credit"]]  # resolved, dismissed
    # Same rule: then by time, in the same direction (the rules' order depends on the collation).
    by_rule = ids(client.get(API, params={"sort": "rule", "order": "asc"}))
    assert by_rule.index(several["router"]) + 1 == by_rule.index(several["r15"])


def test_list_filters(client: TestClient, admin: LoggedIn, several: dict[str, int]) -> None:
    def found(**params: Any) -> set[int]:
        return set(ids(client.get(API, params=params)))

    active = {several["r15"], several["disk"], several["router"], several["info"]}
    assert found(state=["open", "acknowledged"]) == active
    assert found(state="open,acknowledged") == active
    assert found(severity="critical,medium") == {several["disk"], several["conflict"]}
    assert found(rule="R15") == {several["r15"], several["router"]}
    assert found(rule=["R15", "R20-disk"], state="open") == {several["r15"], several["disk"]}
    assert found(q="core-R1") == {several["router"]}  # the source name in the details
    assert found(q="r20-cred") == {several["credit"]}  # the rule id
    assert found(q="ışık-pc") == {several["conflict"]}  # the title, Turkish letters folded
    assert found(q="IŞIK") == {several["conflict"]}
    assert found(q="100%_") == set()  # LIKE wildcards are plain text
    window = {
        "from": (NOW - timedelta(days=1, minutes=1)).isoformat(),
        "to": (NOW - timedelta(hours=1)).isoformat(),
    }
    assert found(**window) == {several["router"], several["conflict"]}


def test_list_refuses_bad_parameters(client: TestClient, admin: LoggedIn) -> None:
    for params in (
        {"state": "closed"},
        {"severity": "urgent"},
        {"rule": "R15; DROP"},
        {"rule": ",".join(f"R{n}" for n in range(21))},  # at most 20 values
        {"sort": "title"},
        {"order": "up"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"from": "2026-10-19T10:00:00"},  # no offset
        {"from": NOW.isoformat(), "to": NOW.isoformat()},
        {"q": "x" * 201},
    ):
        assert error(client.get(API, params=params)) == (422, "validation_error"), params
    # Repeated values collapse.
    assert client.get(API, params={"state": ",".join(["open"] * 30)}).status_code == 200


def test_list_item_shape(client: TestClient, admin: LoggedIn, several: dict[str, int]) -> None:
    items = {item["id"]: item for item in client.get(API).json()["items"]}
    disk = items[several["disk"]]
    assert disk["rule_name_tr"] == "Disk dolmak üzere"
    assert disk["rule_name_en"] == "Disk almost full"
    assert disk["summary_tr"] == "D: diski %96 dolu (eşik %80)."  # from the checked facts
    assert disk["summary_en"] == "Drive D: is 96% full (threshold 80%)."
    assert disk["auto_resolved"] is False
    assert disk["notes_count"] == 0
    assert set(disk) == {
        "id", "time", "created_at", "updated_at", "resolved_at", "rule", "rule_name_tr",
        "rule_name_en", "severity", "state", "title", "summary_tr", "summary_en",
        "auto_resolved", "notes_count",
    }  # fmt: skip
    conflict = items[several["conflict"]]
    assert conflict["auto_resolved"] is True
    assert conflict["summary_tr"] == "Çakışan IP adresi: 172.20.43.120."
    info = items[several["info"]]  # no checked facts: the title, the English rule name
    assert (info["summary_tr"], info["summary_en"]) == ("İmha tamamlandı", "Retention run")


# -- detail ---------------------------------------------------------------------------------------


def test_detail_sanitises_details_and_masks_recipients(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    alert_id = add_alert(
        factory,
        rule="R20-email",
        title="Uyarı e-postası gönderilemedi",
        details={
            "recipients": ["tolga@ornek.example"],
            "smtp_password": "hunter2",  # gitleaks:allow — a fake value that must not show
            "error": "535 auth failed password=hunter2 for user",  # gitleaks:allow
            "message": "x" * 5000,
            "nested": {"api_key": "abc", "ok": [1, 2, 3]},
            "days": [f"2026-10-{n:02d}" for n in range(1, 31)] * 5,
            "title_en": "Alert e-mail could not be sent",
        },
    )
    with factory() as session:
        for kind, recipient, status in (
            ("new", "tolga@ornek.example", "sent"),
            ("new", "it@firma.com.tr", "failed"),
            ("resolved", "garbage", "queued"),
        ):
            session.add(
                AlertDelivery(
                    alert_id=alert_id,
                    kind=kind,
                    recipient=recipient,
                    status=status,
                    attempts=1 if status != "queued" else 0,
                    sent_at=NOW if status == "sent" else None,
                )
            )
        session.commit()
    body = client.get(f"{API}/{alert_id}").json()
    details = body["details"]
    assert details["recipients"] == ["t***@ornek.example"]
    assert details["smtp_password"] == "[gizlendi / redacted]"
    assert details["nested"] == {"api_key": "[gizlendi / redacted]", "ok": [1, 2, 3]}
    assert "hunter2" not in str(body)
    assert details["error"] == "535 auth failed password=*** for user"
    assert details["message"].startswith("x" * 1000)
    assert details["message"].endswith("… (+4000)")
    assert len(details["days"]) == 101
    assert details["days"][-1] == "… (+50)"
    assert body["runbook_tr"].startswith("1. [smtp] ayarlarını")
    assert "test-email" in body["runbook_en"]
    assert [(d["kind"], d["status"], d["recipient_masked"]) for d in body["deliveries"]] == [
        ("new", "sent", "t***@ornek.example"),
        ("new", "failed", "i***@firma.com.tr"),
        ("resolved", "queued", "***"),
    ]
    assert "tolga@" not in str(body["deliveries"])
    assert body["events"] == []
    assert body["summary_en"] == "Alert e-mail could not be sent"  # details.title_en


def test_addresses_inside_texts_are_masked_too(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    """``R20-email`` names the refused recipient in its title (also its summary: it has no
    checked facts), and the SMTP reply echoes it: masked wherever it appears."""
    refusal = "recipient refused: 550 b'5.1.1 <ali.veli@firma.com.tr>: Recipient rejected'"
    title = f"Uyarı e-postası gönderilemedi (3 uyarı, ali.veli@firma.com.tr): {refusal}."
    alert_id = add_alert(
        factory,
        rule="R20-email",
        title=title,
        details={"recipient": "ali.veli@firma.com.tr", "error": refusal, "last_title": title},
    )
    masked = "recipient refused: 550 b'5.1.1 <a***@firma.com.tr>: Recipient rejected'"
    body = client.get(f"{API}/{alert_id}").json()
    assert body["title"] == f"Uyarı e-postası gönderilemedi (3 uyarı, a***@firma.com.tr): {masked}."
    assert body["summary_tr"] == body["title"]
    assert body["details"]["error"] == masked
    assert body["details"]["last_title"] == body["title"]
    assert body["details"]["recipient"] == "a***@firma.com.tr"
    (item,) = [item for item in client.get(API).json()["items"] if item["id"] == alert_id]
    (latest,) = client.get(f"{API}/summary").json()["latest"]
    for shown in (body, item, latest):
        assert "ali.veli@" not in str(shown)


def test_unknown_alert_is_404(client: TestClient, admin: LoggedIn) -> None:
    for method, path, payload in (
        ("GET", f"{API}/999999", None),
        ("POST", f"{API}/999999/state", {"state": "resolved"}),
        ("POST", f"{API}/999999/notes", {"text": "x"}),
    ):
        assert error(client.request(method, path, json=payload)) == (404, "alert_not_found")
    assert error(client.get(f"{API}/0")) == (422, "validation_error")


def test_sanitize_and_mask_helpers() -> None:
    assert sanitize({"Authorization": "Bearer x", "token": "t", "token_sha256": "ab"}) == {
        "Authorization": "[gizlendi / redacted]",
        "token": "[gizlendi / redacted]",
        "token_sha256": "ab",  # a hash, not a secret
    }
    assert sanitize({"key": "write:fortigate"}) == {"key": "write:fortigate"}
    deep: dict[str, Any] = {}
    cursor = deep
    for _ in range(10):
        cursor["a"] = {}
        cursor = cursor["a"]
    assert "…" in str(sanitize(deep))
    assert sanitize({"to": "to: ali+x@firma.com.tr, it@firma.com.tr; root@localhost"}) == {
        "to": "to: a***@firma.com.tr, i***@firma.com.tr; root@localhost"
    }
    assert mask_recipient("x@y") == "x***@y"
    assert mask_recipient("@firma.com.tr") == "***"
    assert mask_recipient("") == "***"


# -- the state workflow ---------------------------------------------------------------------------


@pytest.mark.parametrize(("start", "target"), list(product(ALERT_STATES, ALERT_STATES)))
def test_every_transition(
    client: TestClient,
    admin: LoggedIn,
    factory: sessionmaker[Session],
    start: str,
    target: str,
) -> None:
    alert_id = add_alert(
        factory,
        state=start,
        key=f"R15:switch-{start}",
        resolved_at=NOW if start == "resolved" else None,
        details={"source": "switch-1", "auto_resolved": {"reason": "x"}}
        if start == "resolved"
        else {"source": "switch-1"},
    )
    response = client.post(f"{API}/{alert_id}/state", json={"state": target})
    if start == target:
        assert error(response) == (409, "alert_state_unchanged")
        return
    if target not in TRANSITIONS[start]:
        assert (start, target) in {("resolved", "acknowledged"), ("dismissed", "acknowledged")}
        assert error(response) == (409, "alert_transition_not_allowed")
        assert response.json()["error"]["details"]["allowed"] == sorted(TRANSITIONS[start])
        return
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == target
    assert (body["resolved_at"] is not None) == (target == "resolved")
    assert body["auto_resolved"] is False  # a person's change: no longer the check's
    assert "auto_resolved" not in body["details"]
    (event,) = body["events"]
    assert (event["kind"], event["from_state"], event["to_state"]) == ("state", start, target)
    assert (event["by"], event["text"]) == ("yonetici", None)
    assert datetime.fromisoformat(body["updated_at"]) >= NOW


def test_every_pair_is_tested() -> None:
    """The transition table the docs list (docs/UYARILAR.md section 8)."""
    assert {state: sorted(targets) for state, targets in TRANSITIONS.items()} == {
        "open": ["acknowledged", "dismissed", "resolved"],
        "acknowledged": ["dismissed", "open", "resolved"],
        "resolved": ["dismissed", "open"],
        "dismissed": ["open", "resolved"],
    }


def test_reopen_only_while_no_other_alert_holds_the_key(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    closed = add_alert(factory, state="resolved", key="R15:fortigate", resolved_at=NOW)
    newer = add_alert(factory, state="acknowledged", key="R15:fortigate")
    response = client.post(f"{API}/{closed}/state", json={"state": "open"})
    assert error(response) == (409, "alert_key_active")
    assert response.json()["error"]["details"] == {"alert_id": newer}
    assert f"#{newer}" in response.json()["error"]["message_tr"]
    # Taking the newer one back to "open" is fine: no *other* alert holds the key.
    assert client.post(f"{API}/{newer}/state", json={"state": "open"}).status_code == 200
    assert client.post(f"{API}/{newer}/state", json={"state": "resolved"}).status_code == 200
    reopened = client.post(f"{API}/{closed}/state", json={"state": "open"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["resolved_at"] is None
    # And now the other one cannot be re-opened.
    response = client.post(f"{API}/{newer}/state", json={"state": "open"})
    assert error(response) == (409, "alert_key_active")
    # Alerts without a dedup key never conflict.
    loose = add_alert(factory, state="dismissed", key=None)
    assert client.post(f"{API}/{loose}/state", json={"state": "open"}).status_code == 200


def test_a_concurrent_check_that_opened_the_key_is_a_409(
    client: TestClient,
    admin: LoggedIn,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unique index decides when a check opened an alert between the check and the update."""
    closed = add_alert(factory, state="dismissed", key="R20-disk:D:")
    holder = add_alert(factory, state="open", key="R20-disk:D:")
    real = store.other_active
    calls: list[int] = []

    def blind_once(session: Session, alert: Alert) -> Alert | None:
        calls.append(alert.id)
        return None if len(calls) == 1 else real(session, alert)

    monkeypatch.setattr(store, "other_active", blind_once)
    response = client.post(f"{API}/{closed}/state", json={"state": "open"})
    assert error(response) == (409, "alert_key_active")
    assert response.json()["error"]["details"] == {"alert_id": holder}
    assert client.get(f"{API}/{closed}").json()["state"] == "dismissed"
    assert audits(factory) == []


def test_state_request_validation(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    alert_id = add_alert(factory)
    for payload in (
        {"state": "closed"},
        {},
        {"state": "resolved", "note": "   "},
        {"state": "resolved", "note": "x" * 2001},
        {"state": "resolved", "extra": True},
    ):
        response = client.post(f"{API}/{alert_id}/state", json=payload)
        assert error(response) == (422, "validation_error"), payload
    assert client.get(f"{API}/{alert_id}").json()["state"] == "open"


# -- notes and the audit log ----------------------------------------------------------------------


def test_notes(client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]) -> None:
    alert_id = add_alert(factory, state="resolved", resolved_at=NOW)
    response = client.post(f"{API}/{alert_id}/notes", json={"text": "  İlk not\nikinci satır\t "})
    assert response.status_code == 201, response.text
    event = response.json()
    assert set(event) == {"id", "kind", "from_state", "to_state", "text", "by", "at"}
    assert (event["kind"], event["text"], event["by"]) == (
        "note",
        "İlk not\nikinci satır",
        "yonetici",
    )
    assert (event["from_state"], event["to_state"]) == (None, None)
    longest = client.post(f"{API}/{alert_id}/notes", json={"text": "ş" * 2000})
    assert longest.status_code == 201
    for text in ("", " \n ", "ş" * 2001, "bell\x07", "nul\x00"):
        response = client.post(f"{API}/{alert_id}/notes", json={"text": text})
        assert error(response) == (422, "validation_error"), repr(text)
    detail = client.get(f"{API}/{alert_id}").json()
    assert detail["notes_count"] == 2
    assert detail["state"] == "resolved"  # a note changes no state
    assert [e["kind"] for e in detail["events"]] == ["note", "note"]
    (listed,) = [item for item in client.get(API).json()["items"] if item["id"] == alert_id]
    assert listed["notes_count"] == 2


def test_audit_entries_never_hold_the_note(
    client: TestClient,
    admin: LoggedIn,
    factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    alert_id = add_alert(factory, key="R15:fortigate")
    response = client.post(
        f"{API}/{alert_id}/state", json={"state": "acknowledged", "note": SECRET_NOTE}
    )
    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert [(e["kind"], e["to_state"], e["text"]) for e in events] == [
        ("state", "acknowledged", None),
        ("note", None, SECRET_NOTE),
    ]
    assert response.json()["notes_count"] == 1
    client.post(f"{API}/{alert_id}/notes", json={"text": SECRET_NOTE + " (2)"})
    rows = audits(factory)
    assert [(row.action, row.target, row.details) for row in rows] == [
        ("alert.state", f"alert:{alert_id}", {"from": "open", "to": "acknowledged", "rule": "R15"}),
        ("alert.note", f"alert:{alert_id}", {"length": len(SECRET_NOTE)}),
        ("alert.note", f"alert:{alert_id}", {"length": len(SECRET_NOTE) + 4}),
    ]
    assert {row.actor_username for row in rows} == {"yonetici"}
    with factory() as session:
        everything = [str(row.details) for row in session.scalars(select(AuditLog))]
    assert not any("iz-7Q4K" in text for text in everything)
    spooled = b"".join(path.read_bytes() for path in layout.spool.glob("_audit/*.jsonl"))
    assert b"alert.note" in spooled
    assert b"iz-7Q4K" not in spooled


def test_refused_changes_write_nothing(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    alert_id = add_alert(factory, state="dismissed")
    response = client.post(
        f"{API}/{alert_id}/state", json={"state": "acknowledged", "note": "olmaz"}
    )
    assert error(response) == (409, "alert_transition_not_allowed")
    assert audits(factory) == []
    assert client.get(f"{API}/{alert_id}").json()["events"] == []


# -- system events ---------------------------------------------------------------------------------


def test_automatic_resolution_is_a_system_event(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    alert_id = add_alert(factory, state="acknowledged", key="R15:fortigate")
    with factory() as session:
        resolve(session.get_one(Alert, alert_id), reason="messages_again", now=NOW)
        session.commit()
    body = client.get(f"{API}/{alert_id}").json()
    assert body["auto_resolved"] is True
    assert body["details"]["auto_resolved"]["reason"] == "messages_again"
    (event,) = body["events"]
    assert (event["from_state"], event["to_state"], event["by"], event["text"]) == (
        "acknowledged",
        "resolved",
        "system",
        "messages_again",
    )
    # A person re-opens it: the alert is theirs now.
    body = client.post(f"{API}/{alert_id}/state", json={"state": "open"}).json()
    assert body["auto_resolved"] is False
    assert "auto_resolved" not in body["details"]
    assert [(e["by"], e["to_state"]) for e in body["events"]] == [
        ("system", "resolved"),
        ("yonetici", "open"),
    ]


# -- summary --------------------------------------------------------------------------------------


@pytest.fixture
def closed_history(factory: sessionmaker[Session]) -> None:
    """Closed alerts with events (mean of the three inside the window: 60,000 s)."""
    a = add_alert(factory, state="resolved", created=NOW - timedelta(hours=10), key="a")
    add_event(factory, a, "resolved", NOW - timedelta(hours=9))  # 3,600 s
    b = add_alert(factory, state="dismissed", created=NOW - timedelta(days=5), key="b")
    add_event(factory, b, "resolved", NOW - timedelta(days=5) + timedelta(hours=2))
    add_event(factory, b, "open", NOW - timedelta(days=4), from_state="resolved", actor="tolga")
    add_event(factory, b, "dismissed", NOW - timedelta(days=3), actor="tolga")  # 172,800 s
    c = add_alert(factory, state="resolved", created=NOW - timedelta(days=40), key="c")
    add_event(factory, c, "resolved", NOW - timedelta(days=39))  # outside the 30 days
    d = add_alert(factory, state="open", created=NOW - timedelta(days=2), key="d")
    add_event(factory, d, "resolved", NOW - timedelta(days=1))
    add_event(factory, d, "open", NOW - timedelta(hours=12), from_state="resolved")  # active
    add_alert(factory, state="resolved", created=NOW - timedelta(days=1), key="e")  # no events
    f = add_alert(factory, state="dismissed", created=NOW - timedelta(hours=6), key="f")
    add_event(factory, f, "resolved", NOW - timedelta(hours=5), actor="tolga")  # 3,600 s
    # Reclassified later as a false alarm: no new closing time.
    add_event(factory, f, "dismissed", NOW - timedelta(hours=1), from_state="resolved")


def test_summary_counts_and_mean_time_to_close(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session], closed_history: None
) -> None:
    for severity, count in (("critical", 1), ("high", 2), ("low", 1)):
        for n in range(count):
            add_alert(factory, severity=severity, time=NOW - timedelta(minutes=n + 1))
    add_alert(factory, severity="critical", state="acknowledged", time=NOW)
    add_alert(factory, severity="medium", state="acknowledged", time=NOW - timedelta(days=9))
    body = client.get(f"{API}/summary").json()
    # "high" 3: the two above and the history's re-opened alert "d" (only state open counts).
    assert body["open_by_severity"] == {"critical": 1, "high": 3, "medium": 0, "low": 1, "info": 0}
    assert body["acknowledged"] == 2
    assert len(body["latest"]) == 5
    assert (body["latest"][0]["state"], body["latest"][0]["severity"]) == (
        "acknowledged",
        "critical",
    )  # the newest
    assert all(item["state"] in ("open", "acknowledged") for item in body["latest"])
    assert body["mean_time_to_close_seconds_30d"] == 60000  # (3,600 + 172,800 + 3,600) / 3


def test_summary_without_history(client: TestClient, admin: LoggedIn) -> None:
    body = client.get(f"{API}/summary").json()
    assert body == {
        "open_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
        "acknowledged": 0,
        "latest": [],
        "mean_time_to_close_seconds_30d": None,
    }


def test_mean_time_to_close_from_the_api_workflow(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    # The closing events are stamped with the real clock, not with the module's NOW (taken at
    # import, minutes earlier in a full run): measure from this test's own start.
    start = datetime.now(UTC)
    first = add_alert(factory, created=start - timedelta(hours=2), key="x")
    second = add_alert(factory, created=start - timedelta(hours=4), key="y")
    client.post(f"{API}/{first}/state", json={"state": "resolved"})
    client.post(f"{API}/{second}/state", json={"state": "dismissed"})
    mean = client.get(f"{API}/summary").json()["mean_time_to_close_seconds_30d"]
    elapsed = (datetime.now(UTC) - start).total_seconds()
    assert 3 * 3600 <= mean <= 3 * 3600 + elapsed + 1
