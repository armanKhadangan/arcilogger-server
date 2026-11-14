"""The probe kind ``webhook`` ("Test gönder", Wave 6 contract §2.4.4): the test notification to one
configured webhook, ``ok`` with the status code or ``webhook_test_failed`` with the error text of
§2.4.2. A local HTTPS receiver with its own test CA answers; every value is fictional."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arcilogger_core.admin.probe import (
    FAILURE_CODES,
    TIMEOUTS,
    WEBHOOK_TEST_RULE,
    run_probe,
)
from arcilogger_core.webhook import SIGNATURE_HEADER, signature
from arcilogger_core.webhooktesting import WebhookReceiver

NOW = datetime(2026, 11, 14, 7, 0, tzinfo=UTC)
SECRET = "Örnek-HMAC-Anahtarı-0123456789abcdef"


@pytest.fixture
def receiver(tmp_path: Path) -> Iterator[WebhookReceiver]:
    with WebhookReceiver(tmp_path / "tls") as found:
        yield found


def params(receiver: WebhookReceiver, **webhook: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": "siem",
        "url": receiver.url,
        "ca_file": str(receiver.ca_file),
        "timeout_seconds": 5,
    }
    entry.update(webhook)
    return {
        "webhook": entry,
        "https_proxy": "",
        "version": "0.5.0",
        "site": "arcilogger.ornek.example",
        "web_url": "https://arcilogger.ornek.example",
    }


def test_the_test_notification_is_signed_and_marked_test(receiver: WebhookReceiver) -> None:
    result = run_probe("webhook", params(receiver), {"secret": SECRET})
    assert (result.ok, result.code, result.message) == (True, None, "HTTP 200")
    (request,) = receiver.received
    timestamp = int(request.headers["X-Arcilogger-Timestamp"])
    assert request.headers[SIGNATURE_HEADER] == signature(SECRET, timestamp, request.body)
    alert = json.loads(request.body)["alert"]
    assert alert["rule"] == WEBHOOK_TEST_RULE == "TEST"
    assert (alert["title_tr"], alert["title_en"]) == (
        "ARCILOGGER deneme bildirimi",
        "ARCILOGGER test notification",
    )
    assert alert["url"] == "https://arcilogger.ornek.example"


def test_a_teams_test_reads_its_url_from_the_secrets(receiver: WebhookReceiver) -> None:
    result = run_probe("webhook", params(receiver, url="", format="teams"), {"url": receiver.url})
    assert result.ok
    (request,) = receiver.received
    card = json.loads(request.body)["attachments"][0]["content"]
    assert card["body"][0]["text"] == "Bilgi — ARCILOGGER deneme bildirimi"


def test_a_refusal_is_webhook_test_failed_without_the_secret(receiver: WebhookReceiver) -> None:
    receiver.statuses = [401]
    receiver.reply = f"unknown key {SECRET}".encode()
    result = run_probe("webhook", params(receiver), {"secret": SECRET})
    assert (result.ok, result.code) == (False, "webhook_test_failed")
    assert result.message.startswith("HTTP 401: unknown key ***")
    assert SECRET not in result.message
    assert "kanal=guvenlik" not in result.message


def test_a_generic_test_without_its_secret_sends_nothing(receiver: WebhookReceiver) -> None:
    result = run_probe("webhook", params(receiver), {})
    assert (result.ok, result.code) == (False, "webhook_test_failed")
    assert "HMAC secret is not set" in result.message
    assert receiver.received == []


def test_the_probe_kind_is_registered() -> None:
    assert FAILURE_CODES["webhook"] == "webhook_test_failed"
    assert TIMEOUTS["webhook"] > 60  # the longest timeout_seconds a webhook may have
