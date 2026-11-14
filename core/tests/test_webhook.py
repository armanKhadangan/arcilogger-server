"""Alert webhook payloads, signature and sending (Wave 6 contract §2.4.2-§2.4.3, test T-H2).

``test_webhook_payload_shapes`` pins the three JSON documents (V8: the Teams and Slack shapes are
"sahada doğrula"); the HMAC is checked with a fixed secret and timestamp; a local HTTPS receiver
with its own test CA answers the requests. No payload, no error text holds the secret, the URL's
path or query, or a header value. Every value is fictional.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arcilogger_core.webhook import (
    ERROR_MAX,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    Target,
    WebhookAlert,
    WebhookError,
    encode,
    error_text,
    is_permanent,
    payload,
    send,
    signature,
)
from arcilogger_core.webhooktesting import WebhookReceiver

SECRET = "Örnek-HMAC-Anahtarı-0123456789abcdef"  # 32+ characters, non-ASCII on purpose
NOW = datetime(2026, 11, 12, 6, 41, 7, tzinfo=UTC)
ALERT = WebhookAlert(
    id=412,
    rule="R01",
    severity="high",
    state="open",
    title_tr="İç adres 172.20.41.17 47 farklı porta / 1 adrese engellendi",
    title_en="Internal address 172.20.41.17 was blocked on 47 ports / 1 addresses",
    time=NOW,
    count=47,
    url="https://arcilogger.ornek.example/security?alert=412",
)


def test_webhook_payload_shapes() -> None:
    """The documents of §2.4.3, key for key (V8: Teams and Slack are sahada doğrula)."""
    generic = payload("generic", ALERT, version="0.5.0", site="arcilogger.ornek.example")
    assert generic == {
        "product": "ARCILOGGER",
        "version": "0.5.0",
        "site": "arcilogger.ornek.example",
        "alert": {
            "id": 412,
            "rule": "R01",
            "severity": "high",
            "state": "open",
            "title_tr": ALERT.title_tr,
            "title_en": ALERT.title_en,
            "time": "2026-11-12T09:41:07+03:00",
            "count": 47,
            "url": "https://arcilogger.ornek.example/security?alert=412",
        },
    }
    assert payload("slack", ALERT, version="0.5.0", site="x") == {
        "text": f"[ARCILOGGER] Yüksek — {ALERT.title_tr} ({ALERT.url})"
    }
    assert payload("teams", ALERT, version="0.5.0", site="x") == {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": f"Yüksek — {ALERT.title_tr}", "wrap": True},
                        {
                            "type": "TextBlock",
                            "text": f"High — {ALERT.title_en}",
                            "wrap": True,
                            "isSubtle": True,
                        },
                    ],
                    "actions": [{"type": "Action.OpenUrl", "title": "Aç / Open", "url": ALERT.url}],
                },
            }
        ],
    }


def test_the_body_is_compact_utf8_json() -> None:
    body = encode(payload("generic", ALERT, version="0.5.0", site="s"))
    assert body.decode("utf-8").startswith('{"product":"ARCILOGGER","version":"0.5.0"')
    assert "İç adres".encode() in body  # not \u-escaped
    assert json.loads(body)["alert"]["id"] == 412


def test_the_signature_is_hmac_sha256_of_timestamp_dot_body() -> None:
    body = b'{"product":"ARCILOGGER"}'
    expected = hmac.new(SECRET.encode(), b"1794469267." + body, hashlib.sha256).hexdigest()
    assert signature(SECRET, 1794469267, body) == f"sha256={expected}"
    assert signature(SECRET, 1794469268, body) != signature(SECRET, 1794469267, body)


@pytest.mark.parametrize(
    ("status", "permanent"),
    [(400, True), (401, True), (403, True), (404, True), (410, True), (422, True), (408, False),
     (429, False), (500, False), (502, False), (503, False), (302, False)],
)  # fmt: skip
def test_which_refusal_is_permanent(status: int, permanent: bool) -> None:
    assert is_permanent(status) is permanent


@pytest.fixture
def receiver(tmp_path: Path) -> Iterator[WebhookReceiver]:
    with WebhookReceiver(tmp_path / "tls") as found:
        yield found


def target(receiver: WebhookReceiver, **changes: object) -> Target:
    values: dict[str, object] = {
        "url": receiver.url,
        "format": "generic",
        "secret": SECRET,
        "timeout_seconds": 5,
        "ca_file": receiver.ca_file,
    }
    values.update(changes)
    return Target(**values)  # type: ignore[arg-type]


def test_a_generic_request_is_signed(receiver: WebhookReceiver) -> None:
    body = encode(payload("generic", ALERT, version="0.5.0", site="s"))
    assert send(target(receiver), body, version="0.5.0", now=NOW) == 200
    (request,) = receiver.received
    assert request.path == "/arcilogger?kanal=guvenlik"
    assert request.body == body
    assert request.headers["Content-Type"] == "application/json; charset=utf-8"
    assert request.headers["User-Agent"] == "ARCILOGGER/0.5.0"
    timestamp = request.headers[TIMESTAMP_HEADER]
    assert timestamp == str(int(NOW.timestamp()))
    assert request.headers[SIGNATURE_HEADER] == signature(SECRET, int(timestamp), body)


@pytest.mark.parametrize("kind", ["teams", "slack"])
def test_teams_and_slack_are_not_signed(receiver: WebhookReceiver, kind: str) -> None:
    body = encode(payload(kind, ALERT, version="0.5.0", site="s"))  # type: ignore[arg-type]
    assert send(target(receiver, format=kind, secret=None), body, version="0.5.0", now=NOW) == 200
    (request,) = receiver.received
    assert TIMESTAMP_HEADER not in request.headers
    assert SIGNATURE_HEADER not in request.headers


def test_a_generic_webhook_without_its_secret_sends_nothing(receiver: WebhookReceiver) -> None:
    with pytest.raises(WebhookError, match="HMAC secret is not set") as caught:
        send(target(receiver, secret=None), b"{}", version="0.5.0", now=NOW)
    assert caught.value.permanent is False
    assert receiver.received == []


def test_the_certificate_is_always_verified(receiver: WebhookReceiver) -> None:
    """Without the test CA (the system store) the receiver's certificate is refused."""
    with pytest.raises(WebhookError, match="CERTIFICATE_VERIFY_FAILED") as caught:
        send(target(receiver, ca_file=None), b"{}", version="0.5.0", now=NOW)
    assert caught.value.permanent is False
    assert receiver.received == []


@pytest.mark.parametrize(("status", "permanent"), [(404, True), (429, False), (503, False)])
def test_a_refusal_keeps_the_status_and_the_reply(
    receiver: WebhookReceiver, status: int, permanent: bool
) -> None:
    receiver.statuses = [status]
    receiver.reply = "kanal bulunamadı".encode()
    with pytest.raises(WebhookError) as caught:
        send(target(receiver), b"{}", version="0.5.0", now=NOW)
    assert str(caught.value) == f"HTTP {status}: kanal bulunamadı"
    assert (caught.value.status, caught.value.permanent) == (status, permanent)


def test_a_redirect_is_not_followed(receiver: WebhookReceiver) -> None:
    receiver.statuses = [302]
    with pytest.raises(WebhookError, match="HTTP 302") as caught:
        send(target(receiver), b"{}", version="0.5.0", now=NOW)
    assert caught.value.permanent is False
    assert len(receiver.received) == 1


def test_the_error_never_holds_the_secret_the_query_or_a_header(receiver: WebhookReceiver) -> None:
    """A reply that echoes the request back is cleaned before it is kept."""
    body = b'{"a":1}'
    timestamp = int(NOW.timestamp())
    sent_signature = signature(SECRET, timestamp, body)
    receiver.statuses = [400]
    receiver.reply = (
        f"bad request to {receiver.url} with {SECRET} and {sent_signature} " + "x" * 600
    ).encode()
    with pytest.raises(WebhookError) as caught:
        send(target(receiver), body, version="0.5.0", now=NOW)
    text = str(caught.value)
    assert len(text) <= ERROR_MAX
    assert text.startswith("HTTP 400: bad request to ***")
    for hidden in (
        SECRET,
        sent_signature,
        sent_signature.removeprefix("sha256="),
        "kanal=guvenlik",
    ):
        assert hidden not in text


def test_a_teams_or_slack_url_is_hidden_as_a_whole() -> None:
    url = "https://hooks.ornek.example/services/T000/B000/Gizli"
    found = Target(url=url, format="slack")
    text = error_text(f"cannot reach {url} (hooks.ornek.example)", found.hidden())
    assert "Gizli" not in text
    assert "hooks.ornek.example" not in text


def test_error_text_is_one_short_line() -> None:
    assert error_text("satır 1\r\nsatır 2\tson") == "satır 1 satır 2 son"
    assert len(error_text("x" * 1000)) == ERROR_MAX
