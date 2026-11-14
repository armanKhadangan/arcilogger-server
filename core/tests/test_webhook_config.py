"""``[[webhooks]]`` in ``arcilogger.toml`` (Wave 6 contract §2.4.1, test T-H3): https only, no
user name or password in the URL, no loopback target, unique names, at most ten; the URL of a
Teams or Slack webhook is a secret and stays out of the file; TLS verification cannot be switched
off. Every value is fictional (``*.example`` hosts)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from arcilogger_core.config import (
    WEBHOOK_MAX,
    WebhookConfig,
    load_config,
    webhook_url_problem,
)

BASE = """
data_dir = "D:/ARCILOGGER"
"""
SIEM = """
[[webhooks]]
name = "siem"
url = "https://siem.ornek.example/arcilogger"
"""


def test_a_generic_webhook_with_its_defaults(tmp_path: Path) -> None:
    config = load_config(_file(tmp_path, SIEM))
    (webhook,) = config.webhooks
    assert webhook == WebhookConfig(name="siem", url="https://siem.ornek.example/arcilogger")
    assert (webhook.format, webhook.min_severity, webhook.rules) == ("generic", "high", ())
    assert (webhook.enabled, webhook.timeout_seconds, webhook.ca_file) == (True, 10, None)
    assert webhook.secret_name == "webhook.siem.secret"
    assert webhook.url_name == "webhook.siem.url"


def test_no_webhooks_by_default(tmp_path: Path) -> None:
    assert load_config(_file(tmp_path, "")).webhooks == ()


def test_every_key_of_the_contract(tmp_path: Path) -> None:
    config = load_config(
        _file(
            tmp_path,
            """
[[webhooks]]
name = "siem-2"
url = "https://siem.ornek.example:8443/hook?team=guvenlik"
format = "generic"
min_severity = "medium"
rules = ["R01", "R15", "R20-webhook"]
enabled = false
timeout_seconds = 30
ca_file = "C:/ProgramData/ARCILOGGER/ca/ornek-ca.pem"
""",
        )
    )
    (webhook,) = config.webhooks
    assert webhook.rules == ("R01", "R15", "R20-webhook")
    assert (webhook.min_severity, webhook.enabled, webhook.timeout_seconds) == ("medium", False, 30)
    assert webhook.ca_file == Path("C:/ProgramData/ARCILOGGER/ca/ornek-ca.pem")


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("http://siem.ornek.example/hook", "only https:// is allowed"),
        ("ftp://siem.ornek.example/hook", "only https:// is allowed"),
        ("https:///hook", "the URL names no host"),
        ("https://user:Parola-2026@siem.ornek.example/hook", "user name or password"),
        ("https://user@siem.ornek.example/hook", "user name or password"),
        ("https://127.0.0.1/hook", "loopback"),
        ("https://127.20.0.9:8443/hook", "loopback"),
        ("https://[::1]/hook", "loopback"),
        ("https://[::ffff:127.0.0.1]/hook", "loopback"),
        ("https://siem.ornek.example:99999/hook", "port"),
    ],
)
def test_a_url_that_is_refused(url: str, problem: str) -> None:
    found = webhook_url_problem(url)
    assert found is not None
    assert problem in found


@pytest.mark.parametrize(
    "url",
    [
        "https://siem.ornek.example/hook",
        "https://203.0.113.40:8443/arcilogger",
        "https://[2001:db8::40]/arcilogger",
        "https://localhost.ornek.example/hook",
    ],
)
def test_a_url_that_is_taken(url: str) -> None:
    assert webhook_url_problem(url) is None


def test_the_refusal_never_repeats_the_url(tmp_path: Path) -> None:
    text = SIEM.replace("https://siem", "http://siem").replace("/arcilogger", "/hook?token=Gizli-9")
    with pytest.raises(ValidationError) as caught:
        load_config(_file(tmp_path, text))
    message = str(caught.value)
    assert "only https:// is allowed" in message
    assert "Gizli-9" not in message
    assert "siem.ornek.example" not in message


def test_a_generic_webhook_needs_its_url(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="url is required"):
        load_config(_file(tmp_path, '[[webhooks]]\nname = "siem"\n'))


@pytest.mark.parametrize("kind", ["teams", "slack"])
def test_the_url_of_teams_and_slack_is_a_secret(tmp_path: Path, kind: str) -> None:
    webhook = f'[[webhooks]]\nname = "kanal"\nformat = "{kind}"\n'
    (taken,) = load_config(_file(tmp_path, webhook)).webhooks
    assert (taken.format, taken.url) == (kind, "")
    with_url = webhook + 'url = "https://hooks.ornek.example/services/T000/B000/xyz"\n'
    with pytest.raises(ValidationError, match=r"secret set webhook\.kanal\.url") as caught:
        load_config(_file(tmp_path, with_url))
    assert "xyz" not in str(caught.value)


@pytest.mark.parametrize("name", ["", "SIEM", "siem_1", "siem.1", "s" * 33, "şube"])
def test_a_name_outside_the_pattern(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValidationError):
        load_config(_file(tmp_path, SIEM.replace('"siem"', f'"{name}"')))


def test_names_are_unique(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="webhook names must be unique"):
        load_config(_file(tmp_path, SIEM + SIEM))


def test_at_most_ten(tmp_path: Path) -> None:
    many = "".join(SIEM.replace('"siem"', f'"siem-{n}"') for n in range(WEBHOOK_MAX))
    assert len(load_config(_file(tmp_path, many)).webhooks) == WEBHOOK_MAX
    with pytest.raises(ValidationError, match=f"at most {WEBHOOK_MAX} webhooks"):
        load_config(_file(tmp_path, many + SIEM))


@pytest.mark.parametrize("rules", ['["r01"]', '["R1"]', '["R01", "R01"]', '["R01; DROP"]'])
def test_rules_are_rule_ids(tmp_path: Path, rules: str) -> None:
    with pytest.raises(ValidationError):
        load_config(_file(tmp_path, SIEM + f"rules = {rules}\n"))


@pytest.mark.parametrize(
    "line",
    [
        'min_severity = "urgent"',
        'format = "discord"',
        "timeout_seconds = 0",
        "timeout_seconds = 61",
    ],
)
def test_values_outside_the_contract(tmp_path: Path, line: str) -> None:
    with pytest.raises(ValidationError):
        load_config(_file(tmp_path, SIEM + line + "\n"))


@pytest.mark.parametrize(
    "line",
    [
        "verify_tls = false",
        "tls_verify = false",
        "insecure = true",
        "verify = false",
        'secret = "Gizli-Anahtar-0123456789-0123456789"',
    ],
)
def test_tls_verification_has_no_switch_and_no_secret_lives_in_the_file(
    tmp_path: Path, line: str
) -> None:
    """There is no key to turn the certificate check off, and the HMAC secret is never a key of
    the file (it lives in the secret store)."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_config(_file(tmp_path, SIEM + line + "\n"))


def _file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "arcilogger.toml"
    path.write_text(BASE + text, encoding="utf-8")
    return path
