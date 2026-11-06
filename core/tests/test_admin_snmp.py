"""The browser administration of ``[sources.snmp]`` (Wave 5 §6 N1, W5-N).

A source's SNMP settings travel in its ``source_add``/``source_update`` op; its two keys are
``secret_set``/``secret_delete`` ops of ``snmp.<source>.auth_key`` and ``.priv_key`` in the same
``sources`` request, write-only. Switching SNMP on without both keys is ``snmp_secret_missing``,
on a network source ``snmp_source_is_network``; removing a source deletes its keys.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.admin import rules
from arcilogger_core.admin.contract import FALLBACK_POLICY
from arcilogger_core.admin.rules import RuleContext, SectionCheck, validate_section
from arcilogger_core.admin.tomledit import SetSources, edit
from arcilogger_core.config import Config, SnmpConfig, parse_config
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.timeutil import SITE_TZ

POLICY = FALLBACK_POLICY.model_copy(update={"enabled": True})
NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
TOML = """\
site_name = "Örnek A.Ş."
data_dir = "{data}"

[tsa]
provider = "mock"

[[sources]]
name = "fw-01"
type = "fortigate"
address = "172.20.10.22"

[[sources]]
name = "aps"
type = "generic"
address = "172.20.20.0/24"
"""
SNMP = {"enabled": True, "user": "arcilogger", "interfaces": ["wan1"]}
KEYS = {"snmp_auth_key": "authkeyauthkey", "snmp_priv_key": "privkeyprivkey"}
OFF = SnmpConfig(enabled=False, user="arcilogger", interfaces=("wan1",))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "ProgramData"
    folder.mkdir()
    (folder / "arcilogger.toml").write_text(
        TOML.format(data=(tmp_path / "data").as_posix()), encoding="utf-8"
    )
    return folder


@pytest.fixture
def config(root: Path) -> Config:
    path = root / "arcilogger.toml"
    return parse_config(path.read_bytes(), path)


def context(root: Path, stored: frozenset[str] = frozenset()) -> RuleContext:
    return RuleContext(
        policy=POLICY,
        anchors=TrustAnchors(root, POLICY),
        stored_secrets=stored,
        now=NOW,
        local_addresses=("172.20.10.5",),
    )


def codes(check: SectionCheck) -> list[tuple[str, str | None]]:
    return [(problem.code, problem.field) for problem in check.errors]


def update(snmp: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    source = {"name": "fw-01", "type": "fortigate", "address": "172.20.10.22", "snmp": snmp}
    return {"name": "fw-01", "source": source, **extra}


def test_snmp_on_with_both_keys(root: Path, config: Config) -> None:
    check = validate_section("sources", config, update(SNMP, **KEYS), context(root))
    assert check.ok, check.errors
    kinds = [(op["op"], op.get("name")) for op in check.ops]
    assert kinds == [
        ("source_update", "fw-01"),
        ("secret_set", "snmp.fw-01.auth_key"),
        ("secret_set", "snmp.fw-01.priv_key"),
    ]
    assert check.changes["snmp.fw-01.auth_key"] == {
        "secret": True,
        "old": "unset",
        "new": "set",
        "changed": True,
    }
    assert check.candidate is not None
    (source,) = [s for s in check.candidate.sources if s.name == "fw-01"]
    assert source.snmp is not None
    assert source.snmp.interfaces == ("wan1",)
    text = repr(check.ops) + repr(check.changes)
    assert "authkeyauthkey" not in text  # the keys go encrypted, never in the changes


def test_snmp_on_without_keys_is_refused(root: Path, config: Config) -> None:
    check = validate_section("sources", config, update(SNMP), context(root))
    assert codes(check) == [("snmp_secret_missing", "sources.fw-01.snmp")]
    one = validate_section(
        "sources", config, update(SNMP, snmp_auth_key="authkeyauthkey"), context(root)
    )
    assert codes(one) == [("snmp_secret_missing", "sources.fw-01.snmp")]


def test_stored_keys_are_enough(root: Path, config: Config) -> None:
    stored = frozenset(rules.snmp_keys("fw-01"))
    check = validate_section("sources", config, update(SNMP), context(root, stored))
    assert check.ok, check.errors
    assert [op["op"] for op in check.ops] == ["source_update"]


def test_snmp_off_needs_no_keys(root: Path, config: Config) -> None:
    off = {**SNMP, "enabled": False}
    check = validate_section("sources", config, update(off), context(root))
    assert check.ok, check.errors


@pytest.mark.parametrize("key", ["short", "x" * 65])
def test_a_key_outside_8_to_64_characters_is_refused(root: Path, config: Config, key: str) -> None:
    check = validate_section(
        "sources", config, update(SNMP, snmp_auth_key=key, snmp_priv_key=key), context(root)
    )
    assert ("secret_invalid", "snmp.fw-01.auth_key") in codes(check)


def test_snmp_on_a_network_source_is_refused(root: Path, config: Config) -> None:
    body = {
        "name": "aps",
        "source": {"name": "aps", "type": "generic", "address": "172.20.20.0/24", "snmp": SNMP},
        **KEYS,
    }
    check = validate_section("sources", config, body, context(root))
    assert ("snmp_source_is_network", "sources.aps.snmp") in codes(check)


def test_v1_v2c_and_no_privacy_are_refused(root: Path, config: Config) -> None:
    for snmp in ({**SNMP, "community": "public"}, {**SNMP, "priv": "none"}):
        check = validate_section("sources", config, update(snmp, **KEYS), context(root))
        assert not check.ok
        assert {code for code, _ in codes(check)} == {"source_invalid"}


def test_removing_a_source_deletes_its_keys(root: Path, config: Config) -> None:
    stored = frozenset(rules.snmp_keys("fw-01"))
    body = {"name": "fw-01", "remove": True, "confirm": "fw-01"}
    check = validate_section("sources", config, body, context(root, stored))
    assert check.ok, check.errors
    assert [(op["op"], op.get("name")) for op in check.ops] == [
        ("source_remove", "fw-01"),
        ("secret_delete", "snmp.fw-01.auth_key"),
        ("secret_delete", "snmp.fw-01.priv_key"),
    ]


def test_the_sources_etag_follows_the_keys(root: Path, config: Config) -> None:
    path = root / "arcilogger.toml"
    with_snmp = edit(
        path.read_bytes(),
        [
            SetSources(
                tuple(
                    s.model_copy(update={"snmp": OFF}) if s.name == "fw-01" else s
                    for s in config.sources
                )
            )
        ],
        path=path,
    )
    snmp_config = parse_config(with_snmp, path)
    before = rules.etag("sources", snmp_config, frozenset())
    after = rules.etag("sources", snmp_config, frozenset({"snmp.fw-01.auth_key"}))
    assert before != after
    assert rules.section_secrets("sources", snmp_config) == rules.snmp_keys("fw-01")
    assert rules.section_secrets("sources", config) == ()
