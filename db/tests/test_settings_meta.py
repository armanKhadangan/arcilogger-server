"""``settings_meta`` covers exactly the visible settings, each with Turkish and English texts in
one group of contract 2.13, and agrees with the validation rules."""

from __future__ import annotations

import re

import pytest

from arcilogger_db.settings import (
    DEFAULT_SETTINGS,
    INTEGER_RANGES,
    LOCKED_KEYS,
    REAUTH_KEYS,
    validate_setting,
)
from arcilogger_db.settings_meta import GROUPS, SETTINGS, group_settings, setting_meta

#: Contract 2.13: which keys belong to which group.
GROUP_PREFIXES = {
    "alerts": ("alerts.",),
    "lookup": ("nat.", "lookup.", "lease.", "dhcp.", "arp.", "devices.", "worker.leases_lag_"),
    "usage": ("usage.",),
    "reports": ("reports.",),
    "evidence": ("evidence.",),
    "security": ("auth.", "privacy."),
    "database": ("db.retention_days",),
}
#: Words that would claim legal compliance (the wording rule of the product).
OVERSTATEMENT = re.compile(r"uyumlu|uyumu|garanti|complian|guarantee", re.IGNORECASE)


def test_exactly_the_visible_settings_are_described() -> None:
    assert set(SETTINGS) == set(DEFAULT_SETTINGS)
    assert all(meta.key == key for key, meta in SETTINGS.items())
    order = [group.id for group in GROUPS]
    groups = [meta.group for meta in SETTINGS.values()]
    assert groups == sorted(groups, key=order.index)  # listed group by group


def test_groups_follow_the_contract() -> None:
    assert [group.id for group in GROUPS] == list(GROUP_PREFIXES)
    for group in GROUPS:
        assert group.label_tr
        assert group.label_en
        keys = [meta.key for meta in group_settings(group.id)]
        assert keys, group.id
        assert all(key.startswith(GROUP_PREFIXES[group.id]) for key in keys), group.id


@pytest.mark.parametrize("key", sorted(DEFAULT_SETTINGS))
def test_every_setting_has_turkish_and_english_texts(key: str) -> None:
    meta = setting_meta(key)
    texts = (meta.label_tr, meta.label_en, meta.help_tr, meta.help_en)
    assert all(text.strip() == text and text for text in texts), key
    assert meta.label_tr != meta.label_en
    assert meta.help_tr != meta.help_en
    assert meta.help_tr.endswith(".")
    assert meta.help_en.endswith(".")
    assert not any(len(label) > 60 for label in (meta.label_tr, meta.label_en)), key
    for text in texts:
        assert not OVERSTATEMENT.search(text), (key, text)


@pytest.mark.parametrize("key", sorted(DEFAULT_SETTINGS))
def test_the_metadata_agrees_with_the_validation(key: str) -> None:
    meta = setting_meta(key)
    default = DEFAULT_SETTINGS[key]
    assert meta.default == default
    assert meta.default is not default or not isinstance(default, list | dict)  # a copy
    expected = {bool: "boolean", int: "integer", str: "string", dict: "object"}.get(type(default))
    if isinstance(default, list):
        assert meta.type in ("string_list", "integer_list")
        for item in default:
            assert type(item) is (int if meta.type == "integer_list" else str)
    else:
        assert meta.type == expected
    if meta.type == "integer":
        low, high = INTEGER_RANGES.get(key, (0, None))
        assert (meta.minimum, meta.maximum) == (low, high)
        assert validate_setting(key, low) == low
        if high is not None:
            assert validate_setting(key, high) == high
    else:
        assert (meta.minimum, meta.maximum) == (None, None)
    assert meta.reauth == (key in REAUTH_KEYS)
    assert meta.locked == (key in LOCKED_KEYS)


def test_an_unknown_key_has_no_metadata() -> None:
    with pytest.raises(KeyError):
        setting_meta("audit.spool_watermark")  # an internal value, not a setting
