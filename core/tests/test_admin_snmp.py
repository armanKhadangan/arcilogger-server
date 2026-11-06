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

from arcilogger_core.admin import messages, rules
from arcilogger_core.admin.contract import FALLBACK_POLICY
from arcilogger_core.admin.probe import ChildProber, ProbeResult, probe_snmp
from arcilogger_core.admin.rules import Probe, RuleContext, SectionCheck, validate_section
from arcilogger_core.admin.tomledit import SetSources, edit
from arcilogger_core.config import Config, SnmpConfig, parse_config
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.snmp import IF_HC_IN_OCTETS, IF_HIGH_SPEED, IF_OPER_STATUS, SnmpError, Target
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
#: The two keys of fw-01 and the path of its SNMP table, named once.
AUTH_NAME, PRIV_NAME = rules.snmp_keys("fw-01")
SNMP_PATH = "sources." + "fw-01" + ".snmp"
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
        ("secret_set", AUTH_NAME),
        ("secret_set", PRIV_NAME),
    ]
    assert check.changes[AUTH_NAME] == {
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
    assert codes(check) == [("snmp_secret_missing", SNMP_PATH)]
    one = validate_section(
        "sources", config, update(SNMP, snmp_auth_key="authkeyauthkey"), context(root)
    )
    assert codes(one) == [("snmp_secret_missing", SNMP_PATH)]


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
    assert ("secret_invalid", AUTH_NAME) in codes(check)


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
        ("secret_delete", AUTH_NAME),
        ("secret_delete", PRIV_NAME),
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
    after = rules.etag("sources", snmp_config, frozenset({AUTH_NAME}))
    assert before != after
    assert rules.section_secrets("sources", snmp_config) == rules.snmp_keys("fw-01")
    assert rules.section_secrets("sources", config) == ()


# -- the probe (N5) --------------------------------------------------------------------------------


class _Agent:
    """A device at the library boundary: ifName → (ifIndex, oper, speed, 64-bit counters)."""

    def __init__(self, interfaces: dict[str, tuple[int, int, int, bool]], error: Any = None):
        self.interfaces = interfaces
        self.error = error

    async def walk(self, target: Target, oid: str) -> dict[str, str]:
        if self.error:
            raise SnmpError(self.error, "WrongDigest")
        return {f"{oid}.{index}": name for name, (index, *_rest) in self.interfaces.items()}

    async def get(self, target: Target, oids: Any) -> dict[str, Any]:
        by_index = {index: (oper, speed, hc) for index, oper, speed, hc in self.interfaces.values()}
        values: dict[str, Any] = {}
        for oid in oids:
            column, index = oid.rsplit(".", 1)
            oper, speed, hc = by_index.get(int(index), (None, None, False))
            values[oid] = {
                IF_OPER_STATUS: oper,
                IF_HIGH_SPEED: speed,
                IF_HC_IN_OCTETS: 123 if hc else None,
            }.get(column)
        return values


PARAMS = {
    "host": "172.20.10.22",
    "port": 161,
    "user": "arcilogger",
    "auth": "sha256",
    "priv": "aes128",
    "interfaces": ["wan1"],
}
SECRETS = {"auth_key": "authkeyauthkey", "priv_key": "privkeyprivkey"}


def test_the_probe_lists_the_interfaces() -> None:
    agent = _Agent({"wan1": (3, 1, 1000, True), "lan": (4, 2, 100, False)})
    result = probe_snmp(PARAMS, SECRETS, client=agent)
    assert result == ProbeResult(True, None, "wan1: up, 1000 Mbit/s; lan: down, 100 Mbit/s")


@pytest.mark.parametrize(
    ("agent", "code"),
    [
        (_Agent({"lan": (4, 1, 100, True)}), "snmp_no_such_interface"),
        (_Agent({"wan1": (3, 1, 1000, False)}), "snmp_no_hc_counters"),
        (_Agent({}, error="auth_failed"), "snmp_auth_failed"),
        (_Agent({}, error="timeout"), "snmp_timeout"),
    ],
)
def test_the_probe_names_n4s_reason(agent: _Agent, code: str) -> None:
    result = probe_snmp(PARAMS, SECRETS, client=agent)
    assert (result.ok, result.code) == (False, code)
    assert code in messages.MESSAGES  # each reason has TR/EN text (N5)
    assert "authkeyauthkey" not in result.message


def test_the_child_passes_the_reason_through(tmp_path: Path) -> None:
    """The probe runs in ``python -I -m arcilogger_core.admin.probe``: a port without an agent is
    ``snmp_timeout``, and the keys never come back."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    params = {**PARAMS, "host": "127.0.0.1", "port": port}
    result = ChildProber().run("snmp", params, SECRETS)
    assert (result.ok, result.code) == (False, "snmp_timeout")
    assert "authkeyauthkey" not in result.message


def test_switching_snmp_on_probes_the_source(root: Path, config: Config) -> None:
    check = validate_section("sources", config, update(SNMP, **KEYS), context(root))
    assert check.probes == (Probe("snmp", "fw-01"),)
    stored = frozenset(rules.snmp_keys("fw-01"))
    off = validate_section(
        "sources", config, update({**SNMP, "enabled": False}), context(root, stored)
    )
    assert off.probes == ()
