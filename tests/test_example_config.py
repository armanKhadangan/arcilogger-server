"""The shipped example configuration must always be valid."""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

from arcilogger_core.config import Config, load_config, setup_problems

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "deploy" / "windows" / "arcilogger.example.toml"
LOADGEN = ROOT / "tools" / "loadgen" / "arcilogger_loadgen.py"


def with_example_sources(text: str) -> str:
    """The example with the "#" in front of its commented-out [[sources]] lines removed, as
    docs/DEVICE-SETUP.md section 4 tells an administrator ("# " lines stay comments)."""
    start = text.index("# --- Log sources")
    end = text.index("\n[collector]\n")
    block = re.sub(r"(?m)^#(?=[^ \n]|$)", "", text[start:end])
    return text[:start] + block + text[end:]


def _loadgen() -> ModuleType:
    """tools/loadgen/arcilogger_loadgen.py (as tests/test_loadgen.py loads it)."""
    if (loaded := sys.modules.get("arcilogger_loadgen")) is not None:
        return loaded
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen", LOADGEN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def test_example_config_loads() -> None:
    config = load_config(EXAMPLE)
    assert config.retention.enabled is False
    assert config.retention.days >= 730
    assert config.tsa.legal_grade is False  # test server until the connection test is done
    assert (config.database.host, config.database.name) == ("127.0.0.1", "arcilogger")
    assert (config.api.host, config.api.port) == ("127.0.0.1", 8000)
    # Scheduled database backup (ADR-0009): the example states the defaults.
    assert config.backup == type(config.backup)()
    assert config.backup.dir == Path("backups/scheduled")


def test_example_config_leaves_the_organisation_values_to_the_installer() -> None:
    """The installer writes the organisation name and the local time source into the copy it
    makes (``-SiteName``, ``-NtpServer``; docs/KURULUM.md section 7.1): the example states the
    product defaults for them, which name no organisation (ASSUMPTIONS P1, P2, P5)."""
    config = load_config(EXAMPLE)
    assert config.site_name == Config.model_fields["site_name"].default
    assert config.clock == type(config.clock)()


def test_a_first_installation_configures_no_log_source() -> None:
    """The installer makes a new arcilogger.toml from the example: it must not configure the
    devices of the example network (R15 would alert for devices that never send, a sender at one
    of their addresses would get their parser). Every sender is unknown until the organisation
    adds its own, and config-check says so."""
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "sources" not in tomllib.loads(text)
    config = load_config(EXAMPLE)
    assert config.sources == ()
    assert config.source_name_for("172.31.255.1") is None
    assert "sources" in [problem.key for problem in setup_problems(config)]
    assert re.search(r"(?m)^[ \t]*\[\[[ \t]*sources[ \t]*\]\]", text) is None, "no table line"


def test_the_commented_example_sources_are_a_valid_configuration(tmp_path: Path) -> None:
    """What the example shows as commented-out [[sources]] is the fictional example network: with
    the "#" removed it loads, and only the source block changes."""
    text = EXAMPLE.read_text(encoding="utf-8")
    uncommented = with_example_sources(text)
    path = tmp_path / "arcilogger.toml"
    path.write_text(uncommented, encoding="utf-8")
    config = load_config(path)
    # A small example: a firewall, the core router (the DHCP server) and one switch.
    assert [(s.name, s.type) for s in config.sources] == [
        ("fortigate", "fortigate"),
        ("core-r1", "mikrotik"),
        ("switch-1", "mikrotik"),
    ]
    assert config.source_name_for("172.31.255.1") == "fortigate"
    assert config.source_name_for("172.20.10.1") == "core-r1"
    assert config.source_name_for("172.20.10.21") == "switch-1"
    heartbeats = {source.name: source.heartbeat_minutes for source in config.sources}
    assert (heartbeats["fortigate"], heartbeats["core-r1"]) == (10, 15)
    assert all(source.transport == "any" for source in config.sources), "tcp is commented out"
    others = {key: value for key, value in tomllib.loads(uncommented).items() if key != "sources"}
    assert others == tomllib.loads(text), "only the source block changes"
    assert "sources" not in [problem.key for problem in setup_problems(config)]


def test_a_source_switched_off_is_an_example_value(tmp_path: Path) -> None:
    """The example explains [[sources]] enabled (ADR-0014, Ayarlar -> Kaynaklar): false switches a
    source off without removing it; the default stays true."""
    text = EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"(?m)^#   enabled +false: switched off", text)
    switched_off = with_example_sources(text).replace(
        'name = "switch-1"', 'name = "switch-1"\nenabled = false'
    )
    path = tmp_path / "arcilogger.toml"
    path.write_text(switched_off, encoding="utf-8")
    config = load_config(path)
    assert {source.name: source.enabled for source in config.sources} == {
        "fortigate": True,
        "core-r1": True,
        "switch-1": False,
    }


def test_the_load_generator_sends_from_the_example_sources() -> None:
    """tools/loadgen simulates the example network: its senders are the commented example's
    sources, in the same order."""
    sources = tomllib.loads(with_example_sources(EXAMPLE.read_text(encoding="utf-8")))["sources"]
    example = [(s["name"], s["type"], s["address"]) for s in sources]
    assert [(s.name, s.type, s.address) for s in _loadgen().DEFAULT_SOURCES] == example
