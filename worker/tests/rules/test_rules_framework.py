"""The rules framework of W6-A day 1 (Wave 6 contract §2.1.1, §2.1.2, §2.2): the texts of every
rule, the registry's checks, a rule's declaration, its context's bounds and its evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from ipaddress import ip_network
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from arcilogger_db.models import RULE_EVIDENCE_TABLES, TIME_SERIES_TABLES
from arcilogger_db.settings import SECURITY_RULE_IDS
from arcilogger_worker.rules import registry
from arcilogger_worker.rules.model import (
    Actions,
    EvidenceRef,
    Hit,
    Param,
    RuleContext,
    RuleDef,
    RuleSettings,
)
from arcilogger_worker.rules.texts import COMMON_FIELDS, TEXTS, RuleTexts, title, title_fields

from .cases import CASES

#: Words that would claim legal compliance, or call a security rule a 5651 obligation (§0.1).
OVERSTATEMENT = re.compile(
    r"uyumlu|uyumu|garanti|complian|guarantee|5651 yükümlülü|5651 obligation", re.IGNORECASE
)
NOW = datetime(2026, 11, 12, 9, 45, tzinfo=UTC)


def _nothing(session: Session, context: RuleContext) -> Iterable[Hit]:
    return ()


def _rule(rule_id: str = "R01", changes: dict[str, object] | None = None) -> RuleDef:
    fields: dict[str, object] = {
        "id": rule_id,
        "kind": "event",
        "severity": "high",
        "window_seconds": 60,
        "cooldown_seconds": 0,
        "params": (
            Param("threshold_ports", "int", 30, 1, 65535, "Port eşiği", "Port threshold"),
            Param("deny_actions", "strings", ("deny",), 1, 20, "Eylemler", "Actions"),
        ),
        "inputs": ("fg_traffic",),
        "evaluate": _nothing,
    }
    fields.update(changes or {})
    return RuleDef(**fields)  # type: ignore[arg-type]


def _settings(window_seconds: int) -> RuleSettings:
    return RuleSettings(
        enabled=True,
        severity="high",
        window_seconds=window_seconds,
        cooldown_seconds=0,
        actions=Actions(email=True, webhook=True),
        params={},
    )


def test_the_security_rules_are_r01_to_r19_without_r15() -> None:
    assert len(SECURITY_RULE_IDS) == 18
    assert SECURITY_RULE_IDS[0] == "R01"
    assert SECURITY_RULE_IDS[-1] == "R19"
    assert "R15" not in SECURITY_RULE_IDS


def test_every_rule_has_its_texts() -> None:
    """Contract §2.1.1: ``texts.py`` holds the 18 entries of §2.2 before any rule lands."""
    assert list(TEXTS) == list(SECURITY_RULE_IDS)


@pytest.mark.parametrize("rule_id", SECURITY_RULE_IDS)
def test_the_texts_are_turkish_and_english(rule_id: str) -> None:
    texts = TEXTS[rule_id]
    for text in (
        texts.name_tr,
        texts.name_en,
        texts.title_tr,
        texts.title_en,
        texts.advice_tr,
        texts.advice_en,
    ):
        assert text, rule_id
        assert text.strip() == text, rule_id
        assert not OVERSTATEMENT.search(text), (rule_id, text)
    assert texts.advice_tr != texts.advice_en
    assert texts.title_tr != texts.title_en
    assert texts.advice_tr.endswith(".")
    assert texts.advice_en.endswith(".")
    fields = title_fields(texts)
    # A title names an address, a MAC, a source or an account, never a person (§0.1 rule 5).
    assert not {"person", "person_id", "name", "user_name"} & fields, rule_id
    values = {name: f"<{name}>" for name in fields - COMMON_FIELDS}
    turkish, english = title(rule_id, "172.20.41.17", 47, values)
    assert "{" not in turkish + english


def test_the_titles_of_the_contract() -> None:
    """The two titles §2.2 writes out."""
    assert title("R01", "172.20.41.17", 47, {"ports": 47, "hosts": 3})[0] == (
        "İç adres 172.20.41.17 47 farklı porta / 3 adrese engellendi"
    )
    assert title("R07", "198.51.100.7:8123", 1, {"virus": "EICAR_TEST_FILE", "srcip": "x"}) == (
        "Zararlı yazılım: EICAR_TEST_FILE (x)",
        "Malware: EICAR_TEST_FILE (x)",
    )


def test_title_fields_must_agree() -> None:
    texts = RuleTexts("a", "b", "{key} {ports}", "{key}", "c.", "d.")
    with pytest.raises(ValueError, match="ports"):
        title_fields(texts)
    with pytest.raises(ValueError, match="other language"):
        title_fields(RuleTexts("a", "b", "{reason_en}", "{reason_en}", "c.", "d."))
    texts = RuleTexts("a", "b", "{key} ({reason_tr})", "{key} ({reason_en})", "c.", "d.")
    assert title_fields(texts) == {"key", "reason_tr", "reason_en"}


def test_a_title_without_its_value_names_the_field() -> None:
    with pytest.raises(KeyError, match="ports"):
        title("R01", "172.20.41.17", 47, {"hosts": 3})


def test_the_evidence_tables_are_the_parsed_tables_and_devices() -> None:
    parsed = [table for table in TIME_SERIES_TABLES if table != "messages"]
    assert sorted(RULE_EVIDENCE_TABLES) == sorted([*parsed, "devices"])


def test_the_registry_is_in_id_order_and_complete_per_rule() -> None:
    """Every registered rule has texts, known inputs, and a positive and a negative case (T-A1;
    the rule packages W6-B … W6-E add the rules and their cases)."""
    ids = registry.rule_ids()
    assert list(ids) == [item for item in SECURITY_RULE_IDS if item in ids]
    tables = set(RULE_EVIDENCE_TABLES) | {"leases", "persons"}
    for item in registry.RULES:
        assert registry.rule(item.id) is item
        assert set(item.inputs) <= tables, item.id
        cases = [case for case in CASES if case.rule == item.id]
        assert any(case.positive for case in cases), f"{item.id}: no positive case"
        assert any(not case.positive for case in cases), f"{item.id}: no negative case"
    assert {case.rule for case in CASES} <= set(ids)
    names = [(case.rule, case.name) for case in CASES]
    assert len(names) == len(set(names))
    with pytest.raises(KeyError):
        registry.rule("R15")


def test_the_registry_refuses_twins_strangers_and_rules_without_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="defined twice: R01"):
        registry._collect((_rule("R01"),), (_rule("R01"),))
    with pytest.raises(ValueError, match="not a security rule id: R15"):
        registry._collect((_rule("R15"),))
    monkeypatch.setattr(registry, "TEXTS", {})
    with pytest.raises(ValueError, match="without texts: R02"):
        registry._collect((_rule("R02"),))


def test_the_registry_orders_by_id() -> None:
    rules = registry._collect((_rule("R19"), _rule("R02")), (_rule("R01"),))
    assert [item.id for item in rules] == ["R01", "R02", "R19"]


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"kind": "rule"}, "kind"),
        ({"severity": "urgent"}, "severity"),
        ({"window_seconds": 59}, "window_seconds"),
        ({"window_seconds": 7 * 86_400 + 1}, "window_seconds"),
        ({"cooldown_seconds": -1}, "cooldown_seconds"),
        ({"kind": "condition"}, "clear_seconds"),
        ({"clear_seconds": 600}, "clear_seconds"),
        ({"inputs": ()}, "no inputs"),
        ({"params": (Param("a", "int", 1, label_tr="a", label_en="b"),) * 2}, "duplicate"),
        ({"params": (Param("a", "int", 1),)}, "labels"),
        ({"params": (Param("a", "int", True, label_tr="a", label_en="b"),)}, "integer"),
        ({"params": (Param("a", "strings", ["x"], label_tr="a", label_en="b"),)}, "tuple"),
        ({"params": (Param("a", "bool", 1, label_tr="a", label_en="b"),)}, "bool"),
        ({"params": (Param("a", "int", 0, 1, label_tr="a", label_en="b"),)}, "minimum"),
        ({"params": (Param("a", "ints", (1, 2), 0, 1, label_tr="a", label_en="b"),)}, "maximum"),
        ({"params": (Param("a", "regex", "", label_tr="a", label_en="b"),)}, "kind"),  # type: ignore[arg-type]
    ],
)
def test_a_rule_declaration_is_checked(changes: dict[str, object], problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        _rule(changes=changes)


def test_a_condition_rule_has_its_clear_time() -> None:
    item = _rule("R17", {"kind": "condition", "clear_seconds": 600})
    assert item.clear_seconds == 600
    assert item.needs_setup(_settings(60)) is False


def test_the_context_reads_the_whole_window() -> None:
    """The window ends with the slice; the device-time bounds reach the skew beyond the earlier
    of the slice and the window start (deviation W6-A-1: a window longer than the slice)."""
    skew = timedelta(hours=1)
    long_window = RuleContext(
        settings=_settings(3600),
        start=NOW - timedelta(seconds=600),
        end=NOW,
        max_device_skew=skew,
        internal_networks=(ip_network("172.16.0.0/12"),),
        zone=ZoneInfo("Europe/Istanbul"),
    )
    assert long_window.window_start == NOW - timedelta(hours=1)
    assert long_window.time_low == NOW - timedelta(hours=2)
    assert long_window.time_high == NOW + skew
    short_window = RuleContext(
        settings=_settings(60),
        start=NOW - timedelta(seconds=600),
        end=NOW,
        max_device_skew=skew,
        internal_networks=(),
        zone=ZoneInfo("Europe/Istanbul"),
    )
    assert short_window.time_low == NOW - timedelta(seconds=600) - skew


def test_evidence_is_a_reference_with_its_raw_line() -> None:
    row = EvidenceRef(
        "fg_traffic", 41, NOW, NOW, "fw1", date(2026, 11, 12), raw_line=17, raw_offset=4096
    )
    assert row.raw_line == 17
    device = EvidenceRef("devices", None, NOW)
    assert device.raw_day is None
    with pytest.raises(ValueError, match="raw reference"):
        EvidenceRef("fg_traffic", 41, NOW)
    with pytest.raises(ValueError, match="not a rule evidence table"):
        EvidenceRef("users", 1, NOW)
