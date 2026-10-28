"""The explanation texts of ADR-0016 (FortiGate DHCP evidence, srcmac, internet exits): every
key is in the catalogue, Turkish and English take the same values, every placeholder is
filled, and the settings path they name is the one the web interface shows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from arcilogger_db.settings_meta import GROUPS, SETTINGS
from arcilogger_worker.lookup.texts import ENGINE_EXPLANATIONS, explain

#: The keys ADR-0016 adds (contract 2.11); the rewrites of existing texts come with their engine.
NEW_KEYS = (
    "chain_missing",
    "fortigate_dhcp_not_converted",
    "srcmac_only",
    "srcmac_agrees",
    "srcmac_mismatch",
    "srcmac_ambiguous",
    "srcmac_shared",
    "srcmac_other_lease",
    "srcmac_infrastructure",
    "egress_search",
    "egress_public_unknown",
    "public_ip_no_egress",
    "egress_not_logged",
    "egress_transip_not_seen",
    "egress_interface_not_seen",
    "egress_interface_not_seen_kept",
    "egress_unmapped_seen",
    "egress_unconfigured",
    "nat_unknown_trandisp",
    "fortigate_format_unverified",
    "lease_renewal_not_seen",
    "fortigate_dhcp_not_converted_since_lease",
    "fortigate_dhcp_no_network",
    "egress_observed_not_configured",
    "egress_changed_after_event",
    "session_changed_exit",
    "egress_not_logged_but_seen",
    "dhcp_sources_overlap",
    "fortigate_format_verified",
    "session_from_unknown_source",
    "source_configured_later",
    "egress_public_dynamic",
    "egress_internal",
    "source_clock_offset",
)
#: Retired keys: never emitted again, kept so that stored lookups still render.
RETIRED_KEYS = ("public_ip_not_configured", "wan_transip_not_seen")
#: The one value that differs by language is two placeholders, one per text.
PER_LANGUAGE = {"changes_tr": "changes_en"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")
LOCALES = Path(__file__).resolve().parents[2] / "web" / "src" / "locales"
CLAIMS = re.compile(r"yasal|uyumlu|garanti|complian|guarantee", re.IGNORECASE)


def _placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def _labels(language: str) -> dict[str, Any]:
    """The web interface's labels in ``language`` (``web/src/locales``)."""
    labels: dict[str, Any] = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    return labels


def test_every_new_key_is_in_the_catalogue() -> None:
    assert len(NEW_KEYS) == len(set(NEW_KEYS)) == 34
    assert set(NEW_KEYS) <= set(ENGINE_EXPLANATIONS)
    assert set(RETIRED_KEYS) <= set(ENGINE_EXPLANATIONS)


@pytest.mark.parametrize("key", NEW_KEYS)
def test_both_languages_take_the_same_values(key: str) -> None:
    text_tr, text_en = ENGINE_EXPLANATIONS[key]
    names_tr = {PER_LANGUAGE.get(name, name) for name in _placeholders(text_tr)}
    assert names_tr == _placeholders(text_en), key
    assert text_tr != text_en
    assert text_tr.endswith(".")
    assert text_en.endswith(".")
    assert not CLAIMS.search(text_tr + text_en), key


@pytest.mark.parametrize("key", NEW_KEYS)
def test_every_placeholder_is_filled(key: str) -> None:
    text_tr, text_en = ENGINE_EXPLANATIONS[key]
    names = _placeholders(text_tr) | _placeholders(text_en)
    params: dict[str, str | int | None] = {name: f"<{name}>" for name in names}
    if "at" in names:
        params["at"] = "2026-10-12T08:23:41+00:00"  # times in the Turkish display form
    note = explain(key, **params)
    assert "{" not in note.text_tr + note.text_en
    assert note.params == params
    if "at" in names:
        assert "12.10.2026 11:23:41" in note.text_tr + note.text_en


def test_the_settings_path_is_the_one_the_web_interface_shows() -> None:
    """The texts name the page, the group, the setting and the section by the labels the web
    interface shows in each language (there is no NAT page: the exits are a setting of the
    "5651 lookup and lease timeline" group)."""
    (group,) = [g for g in GROUPS if g.id == "lookup"]
    exits = SETTINGS["nat.egresses"]
    tr, en = _labels("tr"), _labels("en")
    settings_tr = f"{tr['nav']['settings']} → {tr['settings']['sections']['application']}"
    settings_en = f"{en['nav']['settings']} → {en['settings']['sections']['application']}"
    assert settings_tr == "Ayarlar → Uygulama ayarları"
    assert settings_en == "Settings → Application settings"
    path_tr = f"{settings_tr} → {group.label_tr} → {exits.label_tr}"
    path_en = f"{settings_en} → {group.label_en} → {exits.label_en}"
    for key in ("egress_public_unknown", "public_ip_no_egress", "egress_unmapped_seen",
                "egress_unconfigured", "egress_observed_not_configured",
                "egress_not_logged_but_seen", "egress_internal"):  # fmt: skip
        text_tr, text_en = ENGINE_EXPLANATIONS[key]
        assert path_tr in text_tr, key
        assert path_en in text_en, key
    rebuild_tr = f"{settings_tr} → {group.label_tr} → {tr['settings']['leaseRebuild']['title']}"
    rebuild_en = f"{settings_en} → {group.label_en} → {en['settings']['leaseRebuild']['title']}"
    networks_tr = f"{tr['nav']['devices']} → {tr['devices']['tabs']['departments']}"
    networks_en = f"{en['nav']['devices']} → {en['devices']['tabs']['departments']}"
    for key in ("fortigate_dhcp_not_converted_since_lease", "fortigate_dhcp_no_network"):
        text_tr, text_en = ENGINE_EXPLANATIONS[key]
        assert rebuild_tr in text_tr, key
        assert rebuild_en in text_en, key
        assert networks_tr in text_tr, key
        assert networks_en in text_en, key
        assert "Cihazlar" not in text_en
    # A sender configured later (D24) or a logid mapped later is rebuilt from the Sources page
    # and the FortiGate DHCP settings (the manager's final review of PR #30, Q3).
    text_tr, text_en = ENGINE_EXPLANATIONS["fortigate_dhcp_not_converted_since_lease"]
    assert f"FortiGate kaynakları ({tr['nav']['sources']})" in text_tr
    assert f"FortiGate sources ({en['nav']['sources']})" in text_en
    assert "FortiGate DHCP ayarları (ör. logid listesi)" in text_tr
    assert "FortiGate DHCP settings (e.g. the logid list)" in text_en


def test_the_changed_exits_are_one_value_per_language() -> None:
    note = explain(
        "egress_changed_after_event",
        at="2026-10-12T08:23:41+00:00",
        changes_tr="hat-2 (20.10.2026 08:12:00, yonetici; şimdi: genel 198.51.100.77, NAT "
        "100.64.13.9)",
        changes_en="hat-2 (20.10.2026 08:12:00, yonetici; now: public 198.51.100.77, NAT "
        "100.64.13.9)",
    )
    assert "şimdi: genel 198.51.100.77" in note.text_tr
    assert "now: public" not in note.text_tr
    assert "now: public 198.51.100.77" in note.text_en
    assert "şimdi" not in note.text_en
