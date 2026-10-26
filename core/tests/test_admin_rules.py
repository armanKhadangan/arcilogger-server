"""The section rules of the browser administration (ADR-0014; contract 2.3, 2.6, 2.12)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arcilogger_core.admin import messages, rules
from arcilogger_core.admin.contract import FALLBACK_POLICY, AdminPolicy, AdminRequest, SetOp
from arcilogger_core.admin.rules import (
    EDITABLE,
    RuleContext,
    SectionCheck,
    check_request,
    conflicts,
    locked_paths,
    validate_section,
)
from arcilogger_core.config import Config, parse_config
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.timeutil import SITE_TZ

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
POLICY = FALLBACK_POLICY.model_copy(update={"enabled": True})

BASE_TOML = """\
site_name = "Örnek A.Ş."
data_dir = "{data}"

[tsa]
provider = "mock"

[clock]
ntp_server = "172.20.10.1"
fallback_server = "time.ume.tubitak.gov.tr"

[[sources]]
name = "core-r1"
type = "mikrotik"
address = "172.20.10.1"

[[sources]]
name = "aps"
type = "generic"
address = "172.20.20.0/24"
expected = false
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "ProgramData"
    folder.mkdir()
    (folder / "arcilogger.toml").write_text(
        BASE_TOML.format(data=(tmp_path / "data").as_posix()), encoding="utf-8"
    )
    return folder


@pytest.fixture
def config(root: Path) -> Config:
    path = root / "arcilogger.toml"
    return parse_config(path.read_bytes(), path)


def context(root: Path, **changes: Any) -> RuleContext:
    values: dict[str, Any] = {
        "policy": POLICY,
        "anchors": TrustAnchors(root, POLICY),
        "stored_secrets": frozenset(),
        "now": NOW,
        "local_addresses": ("172.20.10.5",),
        **changes,
    }
    return RuleContext(**values)


def codes(check: SectionCheck) -> list[str]:
    return [problem.code for problem in check.errors]


def warnings(check: SectionCheck) -> list[str]:
    return [problem.code for problem in check.warnings]


# -- the allow-list --------------------------------------------------------------------------------


def test_every_editable_path_is_a_configuration_path_or_a_browser_secret() -> None:
    paths = set(rules._leaf_paths(Config)) | set(rules.SECTION_SECRETS["tsa"])
    paths |= {"smtp.password", "replica.password", "sources"}
    assert set(EDITABLE) <= paths
    assert {"data_dir", "secrets_dir"}.isdisjoint(EDITABLE)


def test_every_other_path_is_locked_with_its_reason() -> None:
    locked = {item.path: item.reason for item in locked_paths()}
    assert locked["data_dir"] == "security"
    assert locked["secrets_dir"] == "security"
    assert locked["database.host"] == "security"
    assert locked["database.password"] == "security"
    assert locked["usage.pseudonym_key"] == "security"
    assert locked["backup.pg_dump"] == "security"
    assert locked["api.port"] == "service"
    assert locked["worker.interval_seconds"] == "service"
    assert locked["collector.unknown_senders"] == "collector"
    assert locked["seal.quiet_seconds"] == "evidence"
    assert locked["tsa.digest"] == "expert"
    assert locked["tsa.roots_dir"] == "expert"
    assert locked["clock.ntp_port"] == "expert"
    assert set(locked).isdisjoint(EDITABLE)


@given(
    st.from_regex(r"\A[a-z_]{1,12}(\.[a-z0-9][a-z0-9_-]{0,12}){0,2}\Z").filter(
        lambda path: path not in EDITABLE
    )
)
def test_any_path_outside_the_allow_list_is_locked(path: str) -> None:
    root = Path("/srv/arci")
    config = Config.model_validate({"data_dir": "/srv/data", "secrets_dir": str(root)})
    for section in rules.SECTIONS:
        check = rules._run(
            section,
            config,
            [SetOp(op="set", path=path, expect=None, value="x")],
            RuleContext(
                policy=POLICY,
                anchors=TrustAnchors(root, POLICY),
                stored_secrets=frozenset(),
                now=NOW,
            ),
            test_to=None,
            probe=True,
        )
        assert codes(check) == ["config_key_locked"]
        assert check.edits == ()


def test_a_path_of_another_section_is_locked(root: Path, config: Config) -> None:
    check = validate_section("smtp", config, {"site_name": "x"}, context(root))
    assert check.ops == ()  # a form body names only its own keys
    request = AdminRequest.model_validate(
        {
            "id": "a" * 32, "instance": "b" * 32,
            "issued_at": NOW, "expires_at": NOW.replace(minute=20),
            "kind": "config.apply", "section": "smtp",
            "actor": {"user_id": 3, "username": "ayse", "session": "s", "ip": "172.20.10.37"},
            "ops": [{"op": "set", "path": "site_name", "expect": "Örnek A.Ş.", "value": "x"}],
        }
    )  # fmt: skip
    assert codes(check_request(request, config, context(root))) == ["config_key_locked"]


def test_every_code_the_agent_can_report_has_both_texts() -> None:
    for code in (
        "site_name_required", "host_invalid", "clock_servers_required", "config_invalid",
        "config_key_locked", "retention_below_minimum", "source_overlap", "source_limit",
        "secret_rebind_required", "tsa_would_break", "tsa_not_ready", "java_not_detected",
        "root_exists", "zamane_archive_invalid", "replica_target_invalid", "admin_disabled",
    ):  # fmt: skip
        turkish, english = messages.texts(code)
        assert turkish
        assert english
        assert turkish != english


def test_http_statuses_follow_the_catalogue() -> None:
    assert rules.Problem("source_not_found").http_status == 404
    assert rules.Problem("source_overlap").http_status == 409
    assert rules.Problem("tsa_would_break").http_status == 409
    assert rules.Problem("config_unsupported_format").http_status == 409
    assert rules.Problem("site_name_required").http_status == 422


# -- values, etags, conflicts ----------------------------------------------------------------------


def test_section_values_and_etags(root: Path, config: Config) -> None:
    values = rules.section_values("general", config, frozenset())
    assert values == {
        "site_name": "Örnek A.Ş.",
        "clock.ntp_server": "172.20.10.1",
        "clock.fallback_server": "time.ume.tubitak.gov.tr",
        "clock.alert_offset_ms": 1000,
    }
    tsa = rules.section_values("tsa", config, frozenset({"kamusm.customer"}))
    assert tsa["kamusm.customer"] == "set"
    assert tsa["kamusm.password"] == "unset"
    assert "kamusm.password" not in rules.section_values("general", config, frozenset())
    etag = rules.etag("general", config, frozenset())
    assert etag.startswith("general:")
    assert len(etag) == len("general:") + 64
    changed = config.model_copy(update={"site_name": "Başka"})
    assert rules.etag("general", changed, frozenset()) != etag
    assert rules.etag("tsa", config, frozenset()) != rules.etag(
        "tsa", config, frozenset({"kamusm.password"})
    )
    sources = rules.section_values("sources", config, frozenset())["sources"]
    assert isinstance(sources, list)
    assert isinstance(sources[0], dict)
    assert sources[0]["name"] == "core-r1"


def test_conflicts_name_the_changed_paths(config: Config) -> None:
    ops = [
        SetOp(op="set", path="site_name", expect="Eski", value="Yeni"),
        SetOp(op="set", path="clock.ntp_server", expect="172.20.10.1", value="172.20.10.2"),
        SetOp(op="set", path="data_dir", expect="anything", value="E:/x"),  # locked: no conflict
    ]
    assert conflicts(ops, config, frozenset()) == ["site_name"]


# -- general ---------------------------------------------------------------------------------------


def test_a_site_name_change(root: Path, config: Config) -> None:
    check = validate_section("general", config, {"site_name": "  Örnek Holding  "}, context(root))
    assert check.ok
    assert check.ops == (
        {"op": "set", "path": "site_name", "expect": "Örnek A.Ş.", "value": "Örnek Holding"},
    )
    assert check.changes == {"site_name": {"old": "Örnek A.Ş.", "new": "Örnek Holding"}}
    assert not check.reauth
    assert set(check.effects) == {"reload.api", "reload.worker"}
    assert check.probes == ()
    assert check.candidate is not None
    assert check.candidate.site_name == "Örnek Holding"
    assert not check.guarded
    assert check.alert is None
    assert not check.system_sync


def test_nothing_changed_gives_no_ops(root: Path, config: Config) -> None:
    check = validate_section("general", config, {"site_name": "Örnek A.Ş."}, context(root))
    assert check.ok
    assert check.ops == ()
    assert check.edits == ()


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        ({"site_name": ""}, "site_name_required", "site_name"),
        ({"site_name": "   "}, "site_name_required", "site_name"),
        ({"site_name": "iki\nsatır"}, "config_invalid", "site_name"),
        ({"site_name": "biçim\u200bkarakteri"}, "config_invalid", "site_name"),
        ({"site_name": "x" * 201}, "config_invalid", "site_name"),
        ({"site_name": 7}, "config_invalid", "site_name"),
        ({"clock": {"ntp_server": "10.0.3"}}, "host_invalid", "clock.ntp_server"),
        ({"clock": {"ntp_server": "256.1.1.1"}}, "host_invalid", "clock.ntp_server"),
        ({"clock": {"ntp_server": "kötü host"}}, "host_invalid", "clock.ntp_server"),
        ({"clock": {"fallback_server": "-x"}}, "host_invalid", "clock.fallback_server"),
        ({"clock": {"ntp_server": "", "fallback_server": ""}}, "clock_servers_required",
         "clock.fallback_server"),
        ({"clock": {"alert_offset_ms": 0}}, "config_invalid", "clock.alert_offset_ms"),
        ({"clock": {"alert_offset_ms": 60_001}}, "config_invalid", "clock.alert_offset_ms"),
        ({"clock": "172.20.10.2"}, "config_invalid", "clock"),
    ],
)  # fmt: skip
def test_general_refusals(
    root: Path, config: Config, body: Mapping[str, Any], code: str, field: str
) -> None:
    check = validate_section("general", config, body, context(root))
    assert [(p.code, p.field) for p in check.errors] == [(code, field)]
    assert check.edits == ()
    assert check.candidate is None
    submitted = [v for v in body.values() if isinstance(v, str) and v.strip()]
    assert all(v not in str(p.details) for p in check.errors for v in submitted)  # no echo


def test_a_clock_change_needs_reauthentication_and_is_probed(root: Path, config: Config) -> None:
    body = {"clock": {"ntp_server": "172.20.10.2", "fallback_server": "time.ume.tubitak.gov.tr"}}
    check = validate_section("general", config, body, context(root))
    assert check.ok
    assert check.reauth
    assert check.probes == (rules.Probe("sntp", "172.20.10.2"),)
    assert {"system.time", "task.next_run:ClockCheck"} <= set(check.effects)
    assert check.system_sync
    no_probe = validate_section("general", config, {**body, "probe": False}, context(root))
    assert no_probe.probes == check.probes  # the agent skips them; they are still named


def test_an_empty_local_source_warns_and_strict_mode_skips_the_probe(
    root: Path, config: Config
) -> None:
    body = {"clock": {"ntp_server": "", "fallback_server": "pool.ornek.example"}}
    check = validate_section("general", config, body, context(root, strict_outbound=True))
    assert check.ok
    assert warnings(check) == ["ntp_server_empty", "probe_skipped"]
    assert check.probes == ()
    assert "system.firewall" in check.effects


def test_ipv4_addresses_and_host_names_are_valid_time_servers() -> None:
    for host in ("172.20.10.1", "time.ume.tubitak.gov.tr", "ntp", "a-b.c"):
        assert rules.host_ok(host)
    for host in ("10.0.3", "1.2.3.4.5", "999.1.1.1", "x" * 254, "a_b", "-a"):
        assert not rules.host_ok(host)


def test_the_policy_bounds_nothing_of_general(root: Path, config: Config) -> None:
    tight = AdminPolicy(enabled=True, written_at=NOW, written_by="test", max_sources=1)
    check = validate_section("general", config, {"site_name": "X"}, context(root, policy=tight))
    assert check.ok
