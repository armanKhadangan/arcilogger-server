"""The settings of a rule (Wave 6 contract §2.1.4, test T-A4): every refusal of the strict check
names its ``field``; a stored value that no longer validates is ignored field by field (with a
warning that names the field, never the value) and the default applies."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from ipaddress import ip_network

import pytest
from sqlalchemy.orm import Session

from arcilogger_worker.rules.model import Hit, Param, RuleContext, RuleDef, RuleSettings
from arcilogger_worker.rules.settings import (
    RuleSettingsError,
    as_json,
    defaults,
    effective,
    runs,
    validate_overrides,
)


def _nothing(session: Session, context: RuleContext) -> Iterable[Hit]:
    return ()


def _needs_servers(settings: RuleSettings) -> bool:
    return not settings.params["allowed_dns_servers"]


RULE = RuleDef(
    id="R08",
    kind="event",
    severity="medium",
    window_seconds=3600,
    cooldown_seconds=0,
    params=(
        Param("threshold", "int", 20, 1, 100_000, "Eşik", "Threshold"),
        Param("ports", "ints", (53, 853), 1, 20, "Portlar", "Ports"),
        Param("allowed_dns_servers", "networks", (), 0, 200, "DNS sunucuları", "DNS servers"),
        Param("categories", "strings", ("Proxy Avoidance",), 0, 200, "Kategoriler", "Categories"),
        Param("cpu_percent", "percent", 80, 1, 100, "İşlemci", "CPU"),
        Param("min_bytes", "bytes", 10 * 1024**2, 0, None, "En az bayt", "Least bytes"),
        Param("off_hours", "bool", True, label_tr="Mesai dışı", label_en="Off hours"),
    ),
    inputs=("fg_traffic",),
    evaluate=_nothing,
    needs_setup=_needs_servers,
)


def test_the_defaults() -> None:
    settings = defaults(RULE)
    assert settings.enabled is False  # needs setup with its defaults: idle until configured
    assert settings.severity == "medium"
    assert (settings.window_seconds, settings.cooldown_seconds) == (3600, 0)
    assert settings.actions.email is False  # medium: not e-mailed by default
    assert settings.actions.webhook is True
    assert settings.params["ports"] == (53, 853)
    assert not runs(RULE, settings)


def test_overrides_apply_and_are_normalised() -> None:
    stored = {
        "enabled": True,
        "severity": "high",
        "params": {
            "allowed_dns_servers": ["192.0.2.53/32", "192.0.2.53/32", " 2001:db8::/32"],
            "categories": [" Proxy Avoidance ", "Proxy Avoidance"],
        },
    }
    assert validate_overrides(RULE, stored) == {
        "enabled": True,
        "severity": "high",
        "params": {
            "allowed_dns_servers": ["192.0.2.53/32", "2001:db8::/32"],
            "categories": ["Proxy Avoidance"],
        },
    }
    settings = effective(RULE, stored, strict=True)
    assert settings.actions.email is True  # high: e-mailed by default
    assert settings.params["allowed_dns_servers"] == (
        ip_network("192.0.2.53/32"),
        ip_network("2001:db8::/32"),
    )
    assert runs(RULE, settings)
    assert as_json(settings)["params"]["allowed_dns_servers"] == ["192.0.2.53/32", "2001:db8::/32"]


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ([], "settings"),
        ({"unknown": 1}, "unknown"),
        ({"enabled": 1}, "enabled"),
        ({"severity": "urgent"}, "severity"),
        ({"window_seconds": 59}, "window_seconds"),
        ({"window_seconds": 7 * 86_400 + 1}, "window_seconds"),
        ({"window_seconds": True}, "window_seconds"),
        ({"cooldown_seconds": 7 * 86_400 + 1}, "cooldown_seconds"),
        ({"cooldown_seconds": -1}, "cooldown_seconds"),
        ({"actions": []}, "actions"),
        ({"actions": {"sms": True}}, "actions.sms"),
        ({"actions": {"email": "yes"}}, "actions.email"),
        ({"params": []}, "params"),
        ({"params": {"nope": 1}}, "params.nope"),
        ({"params": {"threshold": 0}}, "params.threshold"),
        ({"params": {"threshold": 100_001}}, "params.threshold"),
        ({"params": {"threshold": "5"}}, "params.threshold"),
        ({"params": {"cpu_percent": 101}}, "params.cpu_percent"),
        ({"params": {"min_bytes": -1}}, "params.min_bytes"),
        ({"params": {"off_hours": "true"}}, "params.off_hours"),
        ({"params": {"ports": []}}, "params.ports"),
        ({"params": {"ports": [53, True]}}, "params.ports"),
        ({"params": {"ports": list(range(21))}}, "params.ports"),
        ({"params": {"categories": ["x" * 201]}}, "params.categories"),
        ({"params": {"categories": [""]}}, "params.categories"),
        ({"params": {"categories": ["a"] * 201}}, "params.categories"),
        ({"params": {"categories": "Proxy"}}, "params.categories"),
        ({"params": {"allowed_dns_servers": ["192.0.2.53/24"]}}, "params.allowed_dns_servers"),
        ({"params": {"allowed_dns_servers": ["dns.example"]}}, "params.allowed_dns_servers"),
        ({"params": {"allowed_dns_servers": [53]}}, "params.allowed_dns_servers"),
    ],
)
def test_every_refusal_names_its_field(value: object, field: str) -> None:
    with pytest.raises(RuleSettingsError) as caught:
        validate_overrides(RULE, value)
    assert caught.value.field == field
    assert caught.value.code == "rule_param_invalid"
    with pytest.raises(RuleSettingsError):
        effective(RULE, value, strict=True)


def test_a_stored_invalid_value_falls_back_field_by_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stored = {
        "enabled": True,
        "severity": "urgent",
        "window_seconds": 600,
        "actions": {"email": "secret-value", "webhook": False},
        "params": {"threshold": -5, "ports": [5353], "allowed_dns_servers": ["192.0.2.53/32"]},
        "colour": "red",
    }
    with caplog.at_level(logging.WARNING):
        settings = effective(RULE, stored)
    assert settings.enabled is True
    assert settings.severity == "medium"  # the default
    assert settings.window_seconds == 600
    assert settings.actions.email is False  # the default for medium
    assert settings.actions.webhook is False
    assert settings.params["threshold"] == 20
    assert settings.params["ports"] == (5353,)
    logged = caplog.text
    for name in ("severity", "actions.email", "params.threshold", "colour"):
        assert name in logged, name
    assert "secret-value" not in logged
    assert "urgent" not in logged


def test_a_stored_value_that_is_not_an_object(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert effective(RULE, "on") == defaults(RULE)
    assert "not an object" in caplog.text
    assert effective(RULE, None) == defaults(RULE)
