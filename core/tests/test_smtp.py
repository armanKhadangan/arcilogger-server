"""The SMTP transport of ``arcilogger_core.smtp`` (ADR-0010, ADR-0014): the trial e-mail, the
classification of failures and the password that never shows in an error text."""

from __future__ import annotations

import email
import email.policy
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import Any

import pytest

from arcilogger_core.admin.testing import SmtpState, SmtpTestServer
from arcilogger_core.config import SmtpConfig
from arcilogger_core.smtp import ERROR_MAX, SmtpSender, SmtpSendError, error_text, trial_message
from arcilogger_core.testing import refused_port
from arcilogger_core.timeutil import SITE_TZ

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
PASSWORD = "smtp-parola-ornek-91"
TO = "bt@ornek.example"


@pytest.fixture
def smtp() -> Any:
    with SmtpTestServer() as state:
        yield state


def settings(state: SmtpState, **values: Any) -> SmtpConfig:
    return SmtpConfig.model_validate(
        {
            "enabled": True,
            "host": state.host,
            "port": state.port,
            "security": "none",
            "from_address": "arcilogger@ornek.example",
            "timeout_seconds": 5,
            **values,
        }
    )


def message() -> EmailMessage:
    return trial_message(
        sender="arcilogger@ornek.example",
        recipient=TO,
        web_url="https://arcilogger.ornek.example",
        now=NOW,
    )


def parsed(data: bytes) -> EmailMessage:
    found = email.message_from_bytes(data, policy=email.policy.default)
    assert isinstance(found, EmailMessage)
    return found


def test_the_trial_message_is_bilingual_and_marked_automatic() -> None:
    mail = trial_message(
        sender="arcilogger@ornek.example",
        recipient=TO,
        web_url='https://arcilogger.ornek.example/?a="b"',
        now=NOW,
    )
    assert mail["Subject"] == "[ARCILOGGER] Deneme e-postası / Test e-mail"
    assert (mail["From"], mail["To"]) == ("arcilogger@ornek.example", TO)
    assert mail["Auto-Submitted"] == "auto-generated"
    assert mail["Message-ID"].endswith("@ornek.example>")
    plain = mail.get_body(preferencelist=("plain",))
    rich = mail.get_body(preferencelist=("html",))
    assert isinstance(plain, EmailMessage)
    assert isinstance(rich, EmailMessage)
    text = str(plain.get_content())
    assert "deneme e-postasıdır" in text
    assert "test e-mail" in text
    assert "Europe/Istanbul" in text
    assert "&quot;b&quot;" in str(rich.get_content())  # the address is escaped in the HTML


def test_a_message_is_delivered(smtp: SmtpState) -> None:
    SmtpSender(settings(smtp)).send(message(), TO)
    [mail] = smtp.received
    assert (mail.sender, mail.recipients, mail.login) == ("arcilogger@ornek.example", [TO], None)
    assert parsed(mail.data)["Subject"] == "[ARCILOGGER] Deneme e-postası / Test e-mail"


def test_auth_plain_with_the_stored_password(smtp: SmtpState) -> None:
    smtp.credentials = ("arcilogger", PASSWORD)
    SmtpSender(settings(smtp, username="arcilogger"), PASSWORD).send(message(), TO)
    assert smtp.received[0].login == ("arcilogger", PASSWORD)


def test_a_password_smtplib_cannot_send_is_a_clear_error(smtp: SmtpState) -> None:
    smtp.credentials = ("arcilogger", "parola-öçş")
    with pytest.raises(SmtpSendError, match="ASCII only") as caught:
        SmtpSender(settings(smtp, username="arcilogger"), "parola-öçş").send(message(), TO)
    assert "öçş" not in str(caught.value)
    assert caught.value.permanent is False


def test_a_refused_password_never_shows_in_the_error(smtp: SmtpState) -> None:
    smtp.credentials = ("arcilogger", "başka")
    smtp.echo = PASSWORD  # a server that echoes what it was sent
    with pytest.raises(SmtpSendError) as caught:
        SmtpSender(settings(smtp, username="arcilogger"), PASSWORD).send(message(), TO)
    error = caught.value
    assert PASSWORD not in str(error)
    assert "***" in str(error)
    assert (error.permanent, error.recipient, error.smtp_code) == (False, False, None)


def test_a_refused_recipient_is_permanent(smtp: SmtpState) -> None:
    smtp.refuse.add(TO)
    with pytest.raises(SmtpSendError) as caught:
        SmtpSender(settings(smtp)).send(message(), TO)
    assert (caught.value.permanent, caught.value.recipient, caught.value.smtp_code) == (
        True,
        True,
        550,
    )
    assert "recipient refused: 550" in str(caught.value)


def test_a_deferred_recipient_is_temporary(smtp: SmtpState) -> None:
    smtp.defer.add(TO)
    with pytest.raises(SmtpSendError) as caught:
        SmtpSender(settings(smtp)).send(message(), TO)
    assert (caught.value.permanent, caught.value.smtp_code) == (False, 451)


def test_settings_that_cannot_send(smtp: SmtpState) -> None:
    incomplete = SmtpConfig.model_validate({"host": None})
    with pytest.raises(SmtpSendError, match="host / from_address"):
        SmtpSender(incomplete).send(message(), TO)
    with pytest.raises(SmtpSendError, match=r"smtp\.password is not stored"):
        SmtpSender(settings(smtp, username="arcilogger")).send(message(), TO)
    assert smtp.received == []


def test_a_server_without_starttls_is_not_used_in_plain_text(smtp: SmtpState) -> None:
    with pytest.raises(SmtpSendError) as caught:
        SmtpSender(settings(smtp, security="starttls"), PASSWORD).send(message(), TO)
    assert caught.value.permanent is False
    assert smtp.received == []


def test_a_silent_server_times_out(smtp: SmtpState) -> None:
    smtp.delay = 2.0
    with pytest.raises(SmtpSendError, match="timed out") as caught:
        SmtpSender(settings(smtp, timeout_seconds=1)).send(message(), TO)
    assert caught.value.permanent is False


def test_nobody_listening_is_temporary() -> None:
    with refused_port() as port:
        config = SmtpConfig.model_validate(
            {"host": "127.0.0.1", "port": port, "security": "none",
             "from_address": "arcilogger@ornek.example", "timeout_seconds": 2}
        )  # fmt: skip
        with pytest.raises(SmtpSendError) as caught:
            SmtpSender(config).send(message(), TO)
    assert (caught.value.permanent, caught.value.smtp_code) == (False, None)


def test_error_texts_are_short_and_without_the_secret() -> None:
    long = smtplib.SMTPResponseException(554, ("x" * 1000 + PASSWORD).encode())
    text = error_text(long, PASSWORD)
    assert len(text) <= ERROR_MAX
    assert text.startswith("SMTPResponseException: 554 ")
    assert error_text(TimeoutError()) == "timeout: no answer from the SMTP server"
    assert PASSWORD not in error_text(OSError(f"refused {PASSWORD}"), PASSWORD)
