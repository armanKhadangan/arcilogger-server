"""The probe kind ``webhook`` ("Test gönder", Wave 6 contract §2.4.4): the test notification to one
configured webhook, ``ok`` with the status code or ``webhook_test_failed`` with the error text of
§2.4.2. A local HTTPS receiver with its own test CA answers; every value is fictional."""

from __future__ import annotations

import json
import socket
import ssl
import time
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arcilogger_core import webhooktesting
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


def _report(what: str, began: float, receiver: WebhookReceiver) -> str:
    """The probe's seconds and the receiver's handshakes, warned on every run so that CI's
    warnings summary shows them (#328, failure 8: a handshake timed out after 5 s on Windows)."""
    report = (
        f"{what}: the probe took {time.monotonic() - began:.3f} s, handshakes "
        f"{[round(seconds, 3) for seconds in receiver.handshakes]} s, "
        f"listening on {receiver.listening}"
    )
    warnings.warn(report, UserWarning, stacklevel=2)
    return report


def test_the_test_notification_is_signed_and_marked_test(receiver: WebhookReceiver) -> None:
    began = time.monotonic()
    result = run_probe("webhook", params(receiver), {"secret": SECRET})
    report = _report("webhook test", began, receiver)
    assert (result.ok, result.code, result.message) == (True, None, "HTTP 200"), report
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


def test_a_client_that_never_handshakes_does_not_hold_the_next(
    receiver: WebhookReceiver,
) -> None:
    """The receiver's handshakes run in their own threads (#328, failure 8): a client that
    connects and never starts TLS does not keep the next one waiting. With the handshake inside
    ``accept()`` the one serving thread waited for the silent client, and the probe's handshake
    timed out (its ``timeout_seconds``, 5)."""
    port = int(receiver.url.split(":")[2].split("/")[0])
    silent = socket.create_connection(("127.0.0.1", port))
    try:
        began = time.monotonic()
        result = run_probe("webhook", params(receiver), {"secret": SECRET})
        report = _report("webhook test beside a silent client", began, receiver)
        assert (result.ok, result.message) == (True, "HTTP 200"), report
    finally:
        silent.close()


def _ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _ipv6_loopback(), reason="this host has no IPv6 loopback")
def test_the_receiver_also_answers_on_the_ipv6_loopback(receiver: WebhookReceiver) -> None:
    """``::1`` serves too, with the same certificate (``localhost``): a client that tries it
    first, as Windows does for ``localhost``, is answered at once (#342: about 2 s otherwise)."""
    assert receiver.listening == ["127.0.0.1", "::1"]
    port = int(receiver.url.split(":")[2].split("/")[0])
    context = ssl.create_default_context(cafile=str(receiver.ca_file))
    with (
        socket.create_connection(("::1", port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname="localhost") as tls,
    ):
        tls.sendall(
            b"POST /over-ipv6 HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n"
            b"Connection: close\r\n\r\n"
        )
        answer = tls.recv(64)
    assert answer.startswith(b"HTTP/1.0 200"), answer
    assert [request.path for request in receiver.received] == ["/over-ipv6"]


def test_without_ipv6_the_receiver_serves_on_ipv4_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where ``::1`` cannot be bound (no IPv6), the IPv4 listener serves alone and the probe
    works as before."""

    def no_ipv6(self: object) -> None:
        raise OSError("no IPv6 here")

    monkeypatch.setattr(webhooktesting._TLSServer6, "server_bind", no_ipv6)
    with WebhookReceiver(tmp_path / "tls") as alone:
        assert alone.listening == ["127.0.0.1"]
        result = run_probe("webhook", params(alone), {"secret": SECRET})
        assert (result.ok, result.message) == (True, "HTTP 200")


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
