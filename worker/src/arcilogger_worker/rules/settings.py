"""The settings of the security rules (Wave 6 contract §2.1.4).

``rules.<ID>`` holds only what differs from the registry defaults::

    {"enabled": true, "severity": "high", "window_seconds": 300, "cooldown_seconds": 3600,
     "actions": {"email": true, "webhook": true}, "params": {"threshold": 5}}

:func:`validate_overrides` is the strict check of the rules API (``PUT /rules/{id}``, the dry
run): the first problem is a :class:`RuleSettingsError` naming its ``field``. :func:`effective`
is what the engine runs with: a stored value that no longer validates (a downgrade, a hand edit
on the console) is ignored **field by field** with a warning in the worker log, and the default
applies. The warning names the rule and the field, never the value.

The engine-wide keys (``rules.lag_seconds`` …, ``rules.internal_networks``) are read by
:func:`engine_settings`, each falling back to its shipped default when the stored value is
refused by :func:`arcilogger_db.settings.validate_setting`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_network
from typing import Any, Final, TypeGuard

from sqlalchemy.orm import Session

from arcilogger_db.models import ALERT_SEVERITIES
from arcilogger_db.settings import (
    DEFAULT_SETTINGS,
    SettingValueError,
    get_settings,
    validate_setting,
)
from arcilogger_worker.rules.model import (
    COOLDOWN_MAX,
    INTEGER_KINDS,
    LIST_KINDS,
    LIST_MAX,
    STRING_MAX,
    WINDOW_MAX,
    WINDOW_MIN,
    Actions,
    Network,
    Param,
    RuleDef,
    RuleSettings,
)

log = logging.getLogger(__name__)

PREFIX: Final = "rules."
#: The keys of a ``rules.<ID>`` object, and of its ``actions``.
TOP_KEYS: Final = frozenset(
    {"enabled", "severity", "window_seconds", "cooldown_seconds", "actions", "params"}
)
ACTION_KEYS: Final = frozenset({"email", "webhook"})
#: Severities whose alerts are e-mailed by default (``actions.email``).
EMAIL_SEVERITIES: Final = frozenset({"high", "critical"})
#: The error code of every refusal (contract §2.6.4).
PARAM_INVALID: Final = "rule_param_invalid"


def setting_key(rule_id: str) -> str:
    """The database setting of ``rule_id``: ``rules.R01``."""
    return f"{PREFIX}{rule_id}"


class RuleSettingsError(ValueError):
    """A ``rules.<ID>`` value the rules API refuses: ``field`` names what is wrong
    (``severity``, ``params.threshold``, ``params.deny_actions``, ``actions.email`` …), never the
    value given."""

    code = PARAM_INVALID

    def __init__(self, field: str) -> None:
        super().__init__(f"invalid rule setting: {field}")
        self.field = field


def _integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _param_value(param: Param, value: object) -> object:
    """The stored form of ``value`` for ``param`` (lists de-duplicated, networks in CIDR form);
    :class:`RuleSettingsError` otherwise."""
    field = f"params.{param.key}"
    if param.kind == "bool":
        if not isinstance(value, bool):
            raise RuleSettingsError(field)
        return value
    if param.kind in INTEGER_KINDS:
        if not _integer(value):
            raise RuleSettingsError(field)
        if param.kind == "percent" and not 0 <= value <= 100:
            raise RuleSettingsError(field)
        if (param.min is not None and value < param.min) or (
            param.max is not None and value > param.max
        ):
            raise RuleSettingsError(field)
        return value
    if not isinstance(value, list) or len(value) > LIST_MAX:
        raise RuleSettingsError(field)
    items: list[Any]
    if param.kind == "ints":
        if not all(_integer(item) for item in value):
            raise RuleSettingsError(field)
        items = list(dict.fromkeys(value))
    elif param.kind == "strings":
        if not all(isinstance(item, str) for item in value):
            raise RuleSettingsError(field)
        texts = [item.strip() for item in value]
        if any(not text or len(text) > STRING_MAX for text in texts):
            raise RuleSettingsError(field)
        items = list(dict.fromkeys(texts))
    else:
        items = list(dict.fromkeys(_network(field, item) for item in value))
    if (param.min is not None and len(items) < param.min) or (
        param.max is not None and len(items) > param.max
    ):
        raise RuleSettingsError(field)
    return items


def _network(field: str, item: object) -> str:
    if not isinstance(item, str) or len(item) > 64:
        raise RuleSettingsError(field)
    try:
        return str(ip_network(item.strip(), strict=True))
    except ValueError:
        raise RuleSettingsError(field) from None


def _seconds(field: str, value: object, low: int, high: int) -> int:
    if not _integer(value):
        raise RuleSettingsError(field)
    if not low <= value <= high:
        raise RuleSettingsError(field)
    return value


def _field(rule: RuleDef, key: str, value: object) -> object:
    """The stored form of the top-level ``key`` of ``rules.<ID>``."""
    if key == "enabled":
        if not isinstance(value, bool):
            raise RuleSettingsError(key)
        return value
    if key == "severity":
        if value not in ALERT_SEVERITIES:
            raise RuleSettingsError(key)
        return value
    if key == "window_seconds":
        return _seconds(key, value, WINDOW_MIN, WINDOW_MAX)
    if key == "cooldown_seconds":
        return _seconds(key, value, 0, COOLDOWN_MAX)
    if key == "actions":
        if not isinstance(value, dict):
            raise RuleSettingsError(key)
        for name, switch in value.items():
            if name not in ACTION_KEYS:
                raise RuleSettingsError(f"actions.{name}")
            if not isinstance(switch, bool):
                raise RuleSettingsError(f"actions.{name}")
        return dict(value)
    # params
    if not isinstance(value, dict):
        raise RuleSettingsError(key)
    params = {param.key: param for param in rule.params}
    stored = {}
    for name, item in value.items():
        if name not in params:
            raise RuleSettingsError(f"params.{name}")
        stored[name] = _param_value(params[name], item)
    return stored


def validate_overrides(rule: RuleDef, value: object) -> dict[str, Any]:
    """The stored form of ``value`` as ``rules.<ID>`` of ``rule``; the first problem raises
    :class:`RuleSettingsError` (unknown keys, a severity outside ``ALERT_SEVERITIES``, a window
    below 60 s or above 7 days, a cooldown above 7 days, a parameter outside its ``min``/``max``,
    a list longer than 200 entries, a string longer than 200 characters, an invalid CIDR)."""
    if not isinstance(value, dict):
        raise RuleSettingsError("settings")
    stored: dict[str, Any] = {}
    for key, item in value.items():
        if key not in TOP_KEYS:
            raise RuleSettingsError(str(key))
        stored[key] = _field(rule, key, item)
    return stored


def _readable(rule: RuleDef, value: object) -> dict[str, Any]:
    """The fields of a stored ``rules.<ID>`` that still validate; each other one is logged and
    left out (its default applies)."""
    if not isinstance(value, dict):
        if value not in (None, {}):
            log.warning("rule %s: the stored settings are not an object; defaults apply", rule.id)
        return {}
    usable: dict[str, Any] = {}
    for key, item in value.items():
        if key not in TOP_KEYS:
            log.warning("rule %s: unknown setting %s ignored", rule.id, key)
            continue
        if key in ("actions", "params") and isinstance(item, dict):
            usable[key] = _readable_entries(rule, key, item)
            continue
        try:
            usable[key] = _field(rule, key, item)
        except RuleSettingsError as exc:
            log.warning("rule %s: invalid setting %s ignored; default applies", rule.id, exc.field)
    return usable


def _readable_entries(rule: RuleDef, key: str, entries: dict[str, Any]) -> dict[str, Any]:
    usable: dict[str, Any] = {}
    for name, item in entries.items():
        try:
            entry = _field(rule, key, {name: item})
        except RuleSettingsError as exc:
            log.warning("rule %s: invalid setting %s ignored; default applies", rule.id, exc.field)
            continue
        if isinstance(entry, dict):
            usable.update(entry)
    return usable


def _runtime(param: Param, value: object) -> object:
    """A parameter as a rule reads it: lists become tuples, networks parsed."""
    if param.kind not in LIST_KINDS or not isinstance(value, list | tuple):
        return value
    if param.kind == "networks":
        return tuple(ip_network(item) for item in value)
    return tuple(value)


def _build(rule: RuleDef, overrides: Mapping[str, Any], enabled_default: bool) -> RuleSettings:
    severity = overrides.get("severity", rule.severity)
    actions = overrides.get("actions", {})
    params = {param.key: param.default for param in rule.params} | overrides.get("params", {})
    by_key = {param.key: param for param in rule.params}
    return RuleSettings(
        enabled=overrides.get("enabled", enabled_default),
        severity=severity,
        window_seconds=overrides.get("window_seconds", rule.window_seconds),
        cooldown_seconds=overrides.get("cooldown_seconds", rule.cooldown_seconds),
        actions=Actions(
            email=actions.get("email", severity in EMAIL_SEVERITIES),
            webhook=actions.get("webhook", True),
        ),
        params={key: _runtime(by_key[key], value) for key, value in params.items()},
    )


def defaults(rule: RuleDef) -> RuleSettings:
    """The registry defaults of ``rule``. ``enabled`` is true unless the rule needs setup with
    them (it stays idle until configured: "Ayar gerekli")."""
    probe = _build(rule, {}, True)
    return _build(rule, {}, not rule.needs_setup(probe))


def effective(rule: RuleDef, stored: object, *, strict: bool = False) -> RuleSettings:
    """The settings ``rule`` runs with: the defaults with the overrides in ``stored`` (the
    value of ``rules.<ID>``). ``strict``: refuse an invalid value (the API, the dry run);
    otherwise ignore each invalid field with a warning (the engine)."""
    overrides = validate_overrides(rule, stored) if strict else _readable(rule, stored)
    return _build(rule, overrides, defaults(rule).enabled)


def runs(rule: RuleDef, settings: RuleSettings) -> bool:
    """Whether the engine evaluates ``rule`` with ``settings``: switched on and configured."""
    return settings.enabled and not rule.needs_setup(settings)


def as_json(settings: RuleSettings) -> dict[str, Any]:
    """``settings`` in the ``rules.<ID>`` shape, every field present (the API's ``defaults``
    and ``settings``, the audit entry ``rule.update``)."""
    return {
        "enabled": settings.enabled,
        "severity": settings.severity,
        "window_seconds": settings.window_seconds,
        "cooldown_seconds": settings.cooldown_seconds,
        "actions": {"email": settings.actions.email, "webhook": settings.actions.webhook},
        "params": {
            key: [str(item) for item in value] if isinstance(value, tuple) else value
            for key, value in settings.params.items()
        },
    }


@dataclass(frozen=True)
class EngineSettings:
    """The engine-wide ``rules.*`` settings."""

    lag: timedelta
    max_slice: timedelta
    max_catch_up: timedelta
    statement_timeout_seconds: int
    failures_alert: int
    evidence_max: int
    no_data: timedelta
    max_device_skew: timedelta
    internal_networks: tuple[Network, ...]
    dry_run_timeout_seconds: int


#: The engine-wide keys (every ``rules.*`` key but the per-rule objects).
ENGINE_KEYS: Final = tuple(
    key for key, value in DEFAULT_SETTINGS.items() if key.startswith(PREFIX) and value != {}
)


def engine_settings(session: Session) -> EngineSettings:
    """The engine-wide settings, each stored value that :func:`validate_setting` refuses
    replaced by its shipped default (logged by key, never by value)."""
    stored = get_settings(session, keys=ENGINE_KEYS)
    values: dict[str, Any] = {}
    for key in ENGINE_KEYS:
        try:
            values[key] = validate_setting(key, stored[key])
        except SettingValueError:
            log.warning("setting %s is invalid; its default applies", key)
            values[key] = DEFAULT_SETTINGS[key]
    return EngineSettings(
        lag=timedelta(seconds=values["rules.lag_seconds"]),
        max_slice=timedelta(seconds=values["rules.max_slice_seconds"]),
        max_catch_up=timedelta(hours=values["rules.max_catch_up_hours"]),
        statement_timeout_seconds=values["rules.statement_timeout_seconds"],
        failures_alert=values["rules.failures_alert"],
        evidence_max=values["rules.evidence_max"],
        no_data=timedelta(hours=values["rules.no_data_hours"]),
        max_device_skew=timedelta(seconds=values["rules.max_device_skew_seconds"]),
        internal_networks=tuple(ip_network(item) for item in values["rules.internal_networks"]),
        dry_run_timeout_seconds=values["rules.dry_run_timeout_seconds"],
    )


def stored_overrides(session: Session, rules: tuple[RuleDef, ...]) -> dict[str, object]:
    """The stored ``rules.<ID>`` value of each of ``rules`` (``{}`` when none is stored)."""
    stored = get_settings(session, keys=[setting_key(rule.id) for rule in rules])
    return {rule.id: stored.get(setting_key(rule.id), {}) for rule in rules}
