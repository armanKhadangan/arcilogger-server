"""The browser administration of ``[sources.snmp]`` (Wave 5 §6 N1, W5-N).

A source's SNMP settings travel in its ``source_add``/``source_update`` op; its two keys are
``secret_set``/``secret_delete`` ops of ``snmp.<source>.auth_key`` and ``.priv_key`` in the same
``sources`` request, write-only. Switching SNMP on without both keys is ``snmp_secret_missing``,
on a network source ``snmp_source_is_network``; removing a source deletes its keys.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue, ValidationError

from arcilogger_core.admin import messages, rules
from arcilogger_core.admin import probe as probe_module
from arcilogger_core.admin.contract import FALLBACK_POLICY, ErrorRef, ProbeInterface
from arcilogger_core.admin.probe import (
    ChildProber,
    ProbeResult,
    bounded_interfaces,
    probe_snmp,
)
from arcilogger_core.admin.rules import Probe, RuleContext, SectionCheck, validate_section
from arcilogger_core.admin.testing import FakeAgent, FakeProber, make_installation
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


def context(
    root: Path, stored: frozenset[str] = frozenset(), *, strict_outbound: bool = False
) -> RuleContext:
    return RuleContext(
        policy=POLICY,
        anchors=TrustAnchors(root, POLICY),
        stored_secrets=stored,
        now=NOW,
        local_addresses=("172.20.10.5",),
        strict_outbound=strict_outbound,
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


def test_snmp_on_an_ipv6_address_is_refused(root: Path, config: Config) -> None:
    body = update(SNMP, **KEYS)
    body["source"]["address"] = "2001:db8::1"
    check = validate_section("sources", config, body, context(root))
    assert codes(check) == [("source_ipv6_unsupported", "sources.fw-01.address")]


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


def with_snmp(root: Path, config: Config, snmp: SnmpConfig) -> Config:
    """``config`` with fw-01's ``[sources.snmp]`` table written as the browser would."""
    path = root / "arcilogger.toml"
    sources = tuple(
        s.model_copy(update={"snmp": snmp}) if s.name == "fw-01" else s for s in config.sources
    )
    return parse_config(edit(path.read_bytes(), [SetSources(sources)], path=path), path)


def test_the_sources_etag_follows_the_keys(root: Path, config: Config) -> None:
    snmp_config = with_snmp(root, config, OFF)
    before = rules.etag("sources", snmp_config, frozenset())
    after = rules.etag("sources", snmp_config, frozenset({AUTH_NAME}))
    assert before != after
    assert rules.section_secrets("sources", snmp_config) == rules.snmp_keys("fw-01")
    assert rules.section_secrets("sources", config) == ()


def test_strict_mode_skips_the_probe_until_the_rule_exists(root: Path, config: Config) -> None:
    """``ARCILOGGER SNMP <source>`` is opened by ApplySystem after the change is written: in
    strict outbound mode a new device address or port is not probed before then."""
    switched_on = validate_section(
        "sources", config, update(SNMP, **KEYS), context(root, strict_outbound=True)
    )
    assert switched_on.ok, switched_on.errors
    assert switched_on.probes == ()
    assert [(w.code, w.field) for w in switched_on.warnings] == [("probe_skipped", SNMP_PATH)]
    assert "system.firewall" in switched_on.effects
    stored = frozenset(rules.snmp_keys("fw-01"))
    on = with_snmp(root, config, OFF.model_copy(update={"enabled": True}))
    for snmp, address in (({**SNMP, "port": 1161}, "172.20.10.22"), (SNMP, "172.20.10.23")):
        body = update(snmp)
        body["source"]["address"] = address
        moved = validate_section("sources", on, body, context(root, stored, strict_outbound=True))
        assert moved.ok, moved.errors
        assert (moved.probes, [w.code for w in moved.warnings]) == ((), ["probe_skipped"])


def test_strict_mode_probes_new_keys_for_the_same_device(root: Path, config: Config) -> None:
    stored = frozenset(rules.snmp_keys("fw-01"))
    on = with_snmp(root, config, OFF.model_copy(update={"enabled": True}))
    check = validate_section(
        "sources", on, update(SNMP, **KEYS), context(root, stored, strict_outbound=True)
    )
    assert check.ok, check.errors
    assert check.probes == (Probe("snmp", "fw-01"),)
    assert check.warnings == ()


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


PARAMS: dict[str, JsonValue] = {
    "host": "172.20.10.22",
    "port": 161,
    "user": "arcilogger",
    "auth": "sha256",
    "priv": "aes128",
    "interfaces": ["wan1"],
}
SECRETS = {"auth_key": "authkeyauthkey", "priv_key": "privkeyprivkey"}


WAN1 = ProbeInterface(name="wan1", oper_status="up", speed_mbps=1000)
LAN = ProbeInterface(name="lan", oper_status="down", speed_mbps=100)


def test_the_probe_lists_the_interfaces() -> None:
    agent = _Agent({"wan1": (3, 1, 1000, True), "lan": (4, 2, 100, False)})
    result = probe_snmp(PARAMS, SECRETS, client=agent)
    message = "wan1: up, 1000 Mbit/s; lan: down, 100 Mbit/s"
    assert result == ProbeResult(True, None, message, (WAN1, LAN))


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
    # once the walk answered, the list of what the device reports goes with it (W5-N-9)
    walked = code in ("snmp_no_such_interface", "snmp_no_hc_counters")
    assert bool(result.interfaces) == walked


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


def test_only_a_change_that_reaches_the_device_probes_it(root: Path, config: Config) -> None:
    """The browser sends a source's stored ``[sources.snmp]`` with every change of it (W5-P-13).
    Switching the source off, or editing its heartbeat, must not wait for a device that may be
    down: only SNMP's settings, the address or new keys of a source that stays on are probed."""
    stored = frozenset(rules.snmp_keys("fw-01"))
    on = with_snmp(root, config, OFF.model_copy(update={"enabled": True}))

    def check(snmp: dict[str, Any], **source: Any) -> SectionCheck:
        body = update(snmp)
        body["source"].update(source)
        result = validate_section("sources", on, body, context(root, stored))
        assert result.ok, result.errors
        return result

    probed = (Probe("snmp", "fw-01"),)
    assert check(SNMP, enabled=False).probes == ()
    assert check(SNMP, heartbeat_minutes=30).probes == ()
    assert check(SNMP, expected=False).probes == ()
    assert check({**SNMP, "interfaces": ["wan1", "wan2"]}).probes == probed
    assert check({**SNMP, "port": 1161}).probes == probed
    assert check(SNMP, address="172.20.10.23").probes == probed
    # New keys: probed while the source stays on, not for a source switched off.
    keyed = validate_section("sources", on, update(SNMP, **KEYS), context(root, stored))
    assert keyed.probes == probed
    body = update(SNMP, **KEYS)
    body["source"]["enabled"] = False
    off = validate_section("sources", on, body, context(root, stored))
    assert off.ok, off.errors
    assert off.probes == ()


# -- the interface list reaches the browser (W5-N-9) -----------------------------------------------


def _through_the_child(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any] | None) -> None:
    """``subprocess.run`` of the parent runs the child's :func:`main` in this process (with
    ``answer`` as the probe's result when given, as a child of an older or a broken build would)."""

    def run(args: list[str], *, input: bytes, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if answer is not None:
            out = json.dumps(answer).encode()
        else:
            stdin, stdout = io.BytesIO(input), io.StringIO()
            with monkeypatch.context() as patched:
                patched.setattr(sys, "stdin", io.TextIOWrapper(stdin))
                patched.setattr(sys, "stdout", stdout)
                probe_module.main()
            out = stdout.getvalue().encode()
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(subprocess, "run", run)  # the parent's call


def test_the_child_sends_the_list_and_the_parent_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _Agent({"lan": (4, 1, 100, True), "wan2": (5, 2, 1000, True)})
    monkeypatch.setattr(probe_module, "PysnmpClient", lambda: agent)
    _through_the_child(monkeypatch, None)
    result = ChildProber().run("snmp", PARAMS, SECRETS)
    assert (result.ok, result.code) == (False, "snmp_no_such_interface")
    assert result.interfaces == (
        ProbeInterface(name="lan", oper_status="up", speed_mbps=100),
        ProbeInterface(name="wan2", oper_status="down", speed_mbps=1000),
    )


def test_the_list_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """257 entries, a 65-character name, a control character, a key as a name, an unknown
    status and a speed that is not a whole number from 0: cut, replaced or left out."""
    raw: list[Any] = [
        {"name": "x" * 65, "oper_status": "up", "speed_mbps": 10},
        {"name": "wan\x1b[1", "oper_status": "sideways", "speed_mbps": -1},
        {"name": SECRETS["auth_key"], "oper_status": None, "speed_mbps": True},
        {"name": "", "oper_status": "up"},
        {"oper_status": "up"},
        "wan9",
    ]
    raw += [{"name": f"if{i}"} for i in range(257 - len(raw))]
    _through_the_child(monkeypatch, {"ok": False, "code": "snmp_no_hc_counters", "interfaces": raw})
    result = ChildProber().run("snmp", PARAMS, SECRETS)
    assert result.code == "snmp_no_hc_counters"
    first, control, secret = result.interfaces[:3]
    assert first == ProbeInterface(name="x" * 64, oper_status="up", speed_mbps=10)
    assert control == ProbeInterface(name="wan?[1", oper_status=None, speed_mbps=None)
    assert secret == ProbeInterface(name="***", oper_status=None, speed_mbps=None)
    assert len(result.interfaces) == 3 + 256 - 6  # the first 256 entries, three left out
    assert bounded_interfaces({"name": "wan1"}, {}) == ()
    assert (
        bounded_interfaces([{"name": "x"}] * 300, {})
        == (ProbeInterface(name="x", oper_status=None, speed_mbps=None),) * 256
    )


def test_only_the_snmp_probe_has_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = [{"name": "wan1"}]
    _through_the_child(monkeypatch, {"ok": False, "code": "x", "interfaces": listed})
    assert ChildProber().run("sntp", {"server": "172.20.10.1"}, {}).interfaces == ()
    _through_the_child(monkeypatch, {"ok": False, "code": "snmp_timeout", "interfaces": listed})
    assert ChildProber().run("snmp", PARAMS, SECRETS).interfaces == (
        ProbeInterface(name="wan1", oper_status=None, speed_mbps=None),
    )


def test_error_ref_takes_a_list_with_the_two_codes_only() -> None:
    for code in ("snmp_no_such_interface", "snmp_no_hc_counters"):
        assert ErrorRef(code=code, interfaces=(WAN1,)).interfaces == (WAN1,)
    assert ErrorRef(code="snmp_timeout").interfaces == ()
    for code in ("snmp_timeout", "ntp_unreachable"):
        with pytest.raises(ValidationError, match="interfaces come only"):
            ErrorRef(code=code, interfaces=(WAN1,))
    with pytest.raises(ValidationError):
        ErrorRef(code="snmp_no_such_interface", interfaces=(WAN1,) * 257)
    for name in ("x" * 65, "wan\n1", ""):
        with pytest.raises(ValidationError):
            ProbeInterface(name=name, oper_status=None, speed_mbps=None)
    with pytest.raises(ValidationError):
        ProbeInterface(name="wan1", oper_status=None, speed_mbps=-1)


@pytest.mark.parametrize(
    ("code", "written"),
    [
        ("snmp_no_such_interface", (WAN1, LAN)),
        ("snmp_no_hc_counters", (WAN1, LAN)),
        ("snmp_timeout", ()),
    ],
)
def test_the_agent_writes_the_list_with_the_error(
    tmp_path: Path, code: str, written: tuple[ProbeInterface, ...]
) -> None:
    keys = dict(zip(rules.snmp_keys("fw-02"), KEYS.values(), strict=True))
    installed = make_installation(tmp_path / "cfg", secrets=keys)
    failure = ProbeResult(False, code, "found: wan1, lan", (WAN1, LAN))
    prober = FakeProber({"snmp": failure})
    source = {"name": "fw-02", "type": "fortigate", "address": "172.20.10.23", "snmp": SNMP}
    request = installed.request(section="sources", ops=[{"op": "source_add", "source": source}])
    installed.submit(request)
    FakeAgent(installed, prober=prober).process()
    status = installed.status(request.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.error.code, status.error.interfaces) == (
        "probe_failed",
        code,
        written,
    )
    probe = [step for step in status.steps if step.step == "probe"]
    assert [(step.state, step.code) for step in probe] == [("failed", code)]  # a code only
