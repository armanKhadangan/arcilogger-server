"""``/api/v1/webhooks`` (Wave 6 contract §2.4.4, W6-H): the webhooks of ``[[webhooks]]`` as the
configuration file holds them now, each with its host only, whether it is ready to send and why
not, its last delivery and its 7-day counts; the deliveries of one webhook; ``webhook_not_found``.
Every value is fictional."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.testing import Installed
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import Alert, AlertDelivery

API = "/api/v1/webhooks"
Login = Callable[..., LoggedIn]
HMAC = "Örnek-HMAC-Anahtarı-0123456789abcdef"  # gitleaks:allow — a fake value
TEAMS_URL = "https://ornek.webhook.office.example/webhookb2/Gizli-Yol?sig=Gizli-Imza"
WEBHOOKS = """
[[webhooks]]
name = "siem"
url = "https://siem.ornek.example/arcilogger?kanal=guvenlik"

[[webhooks]]
name = "teams-guvenlik"
format = "teams"
min_severity = "critical"
rules = ["R06", "R07"]

[[webhooks]]
name = "slack-bt"
format = "slack"
enabled = false

[[webhooks]]
name = "yedek"
url = "https://yedek.ornek.example/hook"
"""


@pytest.fixture
def configured(installed: Installed) -> Installed:
    with installed.config_path.open("a", encoding="utf-8") as handle:
        handle.write(WEBHOOKS)
    installed.store.set("webhook.siem.secret", HMAC)
    installed.store.set("webhook.teams-guvenlik.url", TEAMS_URL)
    return installed


@pytest.fixture
def factory(app_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return app_session_factory


def add_alert(factory: sessionmaker[Session], rule: str = "R01", severity: str = "high") -> int:
    with factory() as session:
        alert = Alert(
            time=utc_now(),
            rule=rule,
            severity=severity,
            state="open",
            title="İç adres 172.20.41.17 47 farklı porta / 1 adrese engellendi",
            details={"title_en": "Internal address 172.20.41.17 was blocked"},
            dedup_key=f"{rule}:{utc_now().timestamp()}",
        )
        session.add(alert)
        session.commit()
        return int(alert.id)


def add_delivery(
    factory: sessionmaker[Session],
    alert_id: int,
    recipient: str,
    status: str,
    *,
    age: timedelta = timedelta(0),
    error: str | None = None,
) -> None:
    created = utc_now() - age
    with factory() as session:
        session.add(
            AlertDelivery(
                alert_id=alert_id,
                kind="webhook",
                recipient=recipient,
                status=status,
                attempts=0 if status == "queued" else 1,
                last_error=error,
                created_at=created,
                sent_at=created if status == "sent" else None,
                next_attempt_at=created + timedelta(minutes=1) if status == "queued" else None,
            )
        )
        session.commit()


def by_name(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in body["webhooks"]}


@pytest.mark.usefixtures("configured")
def test_the_list_shows_host_readiness_and_counts(
    client: TestClient, login_as: Login, factory: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    first, second = add_alert(factory), add_alert(factory, "R06", "critical")
    add_delivery(factory, first, "siem", "sent", age=timedelta(days=8))  # outside the 7 days
    add_delivery(factory, second, "siem", "failed", age=timedelta(hours=2), error="HTTP 404")
    add_delivery(factory, add_alert(factory, "R02"), "siem", "sent", age=timedelta(hours=1))
    add_delivery(factory, first, "yedek", "sent")  # another webhook: not siem's
    response = client.get(API)
    assert response.status_code == 200, response.text
    found = by_name(response.json())
    assert list(found) == ["siem", "teams-guvenlik", "slack-bt", "yedek"]
    siem = found["siem"]
    assert siem["host"] == "siem.ornek.example"
    assert (siem["format"], siem["min_severity"], siem["rules"]) == ("generic", "high", [])
    assert (siem["enabled"], siem["ready"], siem["reason"]) == (True, True, None)
    assert siem["counts_7d"] == {"sent": 1, "failed": 1, "queued": 0}
    assert siem["last_delivery"]["status"] == "sent"
    teams = found["teams-guvenlik"]
    assert teams["host"] == "ornek.webhook.office.example"  # the stored URL's host only
    assert (teams["ready"], teams["reason"], teams["rules"]) == (True, None, ["R06", "R07"])
    assert (teams["last_delivery"], teams["counts_7d"]["sent"]) == (None, 0)
    assert (found["slack-bt"]["ready"], found["slack-bt"]["reason"]) == (False, "disabled")
    assert found["slack-bt"]["host"] == ""  # no URL stored
    assert (found["yedek"]["ready"], found["yedek"]["reason"]) == (False, "secret_missing")
    text = response.text
    for never in ("kanal=guvenlik", "/arcilogger", "Gizli-Yol", "Gizli-Imza", HMAC, "/hook"):
        assert never not in text


def test_a_teams_webhook_without_its_url(
    configured: Installed, client: TestClient, login_as: Login
) -> None:
    configured.store.delete("webhook.teams-guvenlik.url")
    login_as(client, "admin")
    teams = by_name(client.get(API).json())["teams-guvenlik"]
    assert (teams["ready"], teams["reason"], teams["host"]) == (False, "url_missing", "")


def test_no_webhooks_configured(installed: Installed, client: TestClient, login_as: Login) -> None:
    login_as(client, "admin")
    assert client.get(API).json() == {"webhooks": []}


@pytest.mark.usefixtures("configured")
def test_the_deliveries_of_one_webhook_newest_first(
    client: TestClient, login_as: Login, factory: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    first, second = add_alert(factory), add_alert(factory, "R06", "critical")
    add_delivery(factory, first, "siem", "sent", age=timedelta(hours=3))
    add_delivery(factory, second, "siem", "queued", age=timedelta(hours=1), error="HTTP 503")
    add_delivery(factory, first, "yedek", "failed")  # another webhook
    response = client.get(f"{API}/siem/deliveries")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "siem"
    assert [(d["alert_id"], d["rule"], d["status"]) for d in body["items"]] == [
        (second, "R06", "queued"),
        (first, "R01", "sent"),
    ]
    queued = body["items"][0]
    assert (queued["severity"], queued["attempts"], queued["last_error"]) == (
        "critical",
        0,
        "HTTP 503",
    )
    assert queued["next_attempt_at"] is not None
    assert queued["sent_at"] is None
    assert len(client.get(f"{API}/siem/deliveries", params={"limit": 1}).json()["items"]) == 1


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("name", ["nope", "Siem", "siem.1"])
def test_an_unknown_webhook(client: TestClient, login_as: Login, name: str) -> None:
    login_as(client, "admin")
    response = client.get(f"{API}/{name}/deliveries")
    if response.status_code == 404:
        error = response.json()["error"]
        assert error["code"] == "webhook_not_found"
        assert (error["message_tr"], error["message_en"]) == (
            "Böyle bir webhook yok.",
            "No such webhook.",
        )
    else:
        assert response.status_code == 422  # not a webhook name at all


@pytest.mark.usefixtures("configured")
def test_the_list_needs_alerts_read(client: TestClient, login_as: Login) -> None:
    assert client.get(API).status_code == 401
    login_as(client, "viewer")
    assert client.get(API).status_code == 403
    assert client.get(f"{API}/siem/deliveries").status_code == 403
