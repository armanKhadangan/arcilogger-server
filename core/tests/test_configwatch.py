"""Live reload of the configuration file (ADR-0014; contract 2.8.2, 2.4.12)."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core import configwatch
from arcilogger_core.config import Config, load_config, parse_config
from arcilogger_core.configwatch import (
    API_HOT,
    COLLECTOR_HOT,
    COLLECTOR_RESTART,
    INGEST_HOT,
    INGEST_RESTART,
    WORKER_HOT,
    ConfigWatcher,
    config_status_of,
)

TEXT = """site_name = "Örnek A.Ş."
data_dir = "D:/ARCILOGGER"

[[sources]]
name = "core-r1"
type = "mikrotik"
address = "172.20.10.1"

[collector]
bind = "172.20.10.5"
"""
SWITCH = '\n[[sources]]\nname = "switch-2"\ntype = "mikrotik"\naddress = "172.20.10.22"\n'
START = datetime(2026, 10, 25, 7, 15, tzinfo=UTC)


class Clocks:
    def __init__(self) -> None:
        self.seconds = 1000.0
        self.now = START

    def monotonic(self) -> float:
        return self.seconds

    def wall(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.seconds += seconds
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clocks() -> Clocks:
    return Clocks()


@pytest.fixture
def path(tmp_path: Path) -> Path:
    file = tmp_path / "arcilogger.toml"
    file.write_text(TEXT, encoding="utf-8")
    return file


def watcher(path: Path, clocks: Clocks, **kwargs: Any) -> ConfigWatcher:
    kwargs.setdefault("hot", COLLECTOR_HOT)
    kwargs.setdefault("restart", COLLECTOR_RESTART)
    return ConfigWatcher(
        path,
        load_config(path),
        min_interval=5.0,
        monotonic=clocks.monotonic,
        now=clocks.wall,
        **kwargs,
    )


def rewrite(path: Path, text: str) -> None:
    """Write new content with a new modification time (as an editor or a replace does)."""
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_start_reports_the_file_it_was_loaded_from(path: Path, clocks: Clocks) -> None:
    watch = watcher(path, clocks).poll()
    assert watch.config == load_config(path)
    assert (watch.changed, watch.sha256, watch.error, watch.restart_keys) == (
        False,
        sha(path),
        None,
        (),
    )
    assert watch.loaded_at == START


def test_a_hot_key_is_taken_after_the_interval(path: Path, clocks: Clocks) -> None:
    watch = watcher(path, clocks)
    started = watch.poll().config
    rewrite(path, TEXT + SWITCH)
    clocks.advance(4.9)
    assert watch.poll().config is started, "not before min_interval"
    clocks.advance(0.2)
    reloaded = watch.poll()
    assert reloaded.changed
    assert [source.name for source in reloaded.config.sources] == ["core-r1", "switch-2"]
    assert reloaded.config.source_name_for("172.20.10.22") == "switch-2"
    assert reloaded.config.collector is started.collector
    assert (reloaded.sha256, reloaded.loaded_at) == (sha(path), clocks.now)
    clocks.advance(10)
    again = watch.poll()
    assert not again.changed
    assert again.config is reloaded.config


def test_switching_a_source_off_is_taken_live(path: Path, clocks: Clocks) -> None:
    """``[[sources]] enabled = false`` (ADR-0014) is part of the hot ``sources``; a source
    without the key stays on."""
    watch = watcher(path, clocks)
    assert watch.poll().config.sources[0].enabled is True
    rewrite(
        path, TEXT.replace('address = "172.20.10.1"', 'address = "172.20.10.1"\nenabled = false')
    )
    clocks.advance(5)
    off = watch.poll()
    assert off.changed
    assert off.config.sources[0].enabled is False
    assert off.config.sources[0].model_dump()["enabled"] is False
    assert off.restart_keys == ()


def test_a_broken_file_keeps_the_running_configuration(
    path: Path, clocks: Clocks, caplog: pytest.LogCaptureFixture
) -> None:
    watch = watcher(path, clocks)
    good = watch.poll()
    rewrite(path, TEXT + SWITCH + "[[sources]\n")
    clocks.advance(5)
    with caplog.at_level(logging.ERROR, logger="arcilogger.configwatch"):
        broken = watch.poll()
        os.utime(path, ns=(1, 1))  # touched, still the same broken bytes
        clocks.advance(5)
        assert watch.poll().error == broken.error
    assert broken.config is good.config
    assert (broken.changed, broken.sha256) == (False, good.sha256)
    assert broken.error is not None
    assert "TOMLDecodeError" in broken.error
    assert len([r for r in caplog.records if "not loaded" in r.getMessage()]) == 1, "logged once"
    rewrite(path, TEXT + SWITCH)
    clocks.advance(5)
    fixed = watch.poll()
    assert (fixed.changed, fixed.error, fixed.sha256) == (True, None, sha(path))


def test_an_invalid_value_is_reported_on_one_line(path: Path, clocks: Clocks) -> None:
    watch = watcher(path, clocks)
    rewrite(
        path,
        TEXT.replace('bind = "172.20.10.5"', 'bind = "172.20.10.5"\nudp_port = 99999') + SWITCH,
    )
    clocks.advance(5)
    result = watch.poll()
    assert result.error == "collector.udp_port: Input should be less than or equal to 65535"
    assert [source.name for source in result.config.sources] == ["core-r1"], "nothing taken"
    rewrite(path, TEXT.replace('address = "172.20.10.1"', 'address = "172.20.10.999"'))
    clocks.advance(5)
    error = watch.poll().error
    assert error is not None
    assert error.startswith("sources.0.address: ")
    assert "\n" not in error


def test_restart_keys_are_reported_and_not_taken(path: Path, clocks: Clocks) -> None:
    watch = watcher(path, clocks)
    rewrite(path, TEXT.replace('bind = "172.20.10.5"', 'bind = "172.20.10.6"'))
    clocks.advance(5)
    result = watch.poll()
    assert (result.changed, result.restart_keys, result.sha256) == (
        False,
        ("collector",),
        sha(path),
    )
    assert result.config.collector.bind == "172.20.10.5"
    # It stays reported until a restart (or until the file says the old value again).
    rewrite(path, TEXT.replace('bind = "172.20.10.5"', 'bind = "172.20.10.6"') + SWITCH)
    clocks.advance(5)
    both = watch.poll()
    assert both.changed
    assert both.restart_keys == ("collector",)


def test_keys_a_service_does_not_read_are_neither_taken_nor_reported(
    path: Path, clocks: Clocks
) -> None:
    """A site name change from the browser reaches the collector as a new hash only."""
    watch = watcher(path, clocks)
    rewrite(path, TEXT.replace("Örnek A.Ş.", "Örnek Holding A.Ş."))
    clocks.advance(5)
    result = watch.poll()
    assert (result.changed, result.restart_keys, result.sha256) == (False, (), sha(path))
    assert result.config.site_name == "Örnek A.Ş."


def test_the_worker_takes_every_key_but_its_own_and_the_database(
    path: Path, clocks: Clocks
) -> None:
    watch = watcher(path, clocks, hot=WORKER_HOT, restart=None)
    rewrite(path, TEXT.replace("Örnek A.Ş.", "Örnek Holding A.Ş.") + "\n[database]\nport = 5433\n")
    clocks.advance(5)
    result = watch.poll()
    assert result.changed
    assert result.config.site_name == "Örnek Holding A.Ş."
    assert result.config.database.port == 5432
    assert result.restart_keys == ("database",)


def test_a_touched_file_is_not_parsed_again(
    path: Path, clocks: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch = watcher(path, clocks)
    calls: list[Path] = []
    real = parse_config

    def counting(data: bytes, where: Path) -> Config:
        calls.append(where)
        return real(data, where)

    monkeypatch.setattr(configwatch, "parse_config", counting)
    os.utime(path, ns=(10**18, 10**18))
    clocks.advance(5)
    result = watch.poll()
    assert (result.changed, result.sha256, calls) == (False, sha(path), [])


def test_a_file_missing_for_a_moment_keeps_the_configuration(path: Path, clocks: Clocks) -> None:
    watch = watcher(path, clocks)
    good = watch.poll()
    path.unlink()
    clocks.advance(5)
    missing = watch.poll()
    assert missing.config is good.config
    assert missing.error is not None
    assert "FileNotFoundError" in missing.error
    path.write_text(TEXT, encoding="utf-8")
    clocks.advance(5)
    back = watch.poll()
    assert (back.changed, back.error, back.sha256) == (False, None, good.sha256)


def test_a_configuration_not_from_the_file_is_replaced_at_the_first_poll(
    path: Path, clocks: Clocks
) -> None:
    older = load_config(path)
    rewrite(path, TEXT + SWITCH)
    watch = ConfigWatcher(
        path,
        older,
        hot=COLLECTOR_HOT,
        restart=COLLECTOR_RESTART,
        min_interval=60,
        monotonic=clocks.monotonic,
        now=clocks.wall,
    )
    first = watch.poll()
    assert first.changed, "no wait for the interval"
    assert (len(first.config.sources), first.sha256) == (2, sha(path))


def test_a_watch_never_raises(
    path: Path, clocks: Clocks, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    watch = watcher(path, clocks)
    good = watch.poll()

    def broken(data: bytes, where: Path) -> Config:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(configwatch, "parse_config", broken)
    rewrite(path, TEXT + SWITCH)
    clocks.advance(5)
    with caplog.at_level(logging.ERROR, logger="arcilogger.configwatch"):
        result = watch.poll()
    assert result.config is good.config
    assert result.error == "RuntimeError: unexpected"


def test_the_status_block_round_trips(path: Path, clocks: Clocks) -> None:
    watch = watcher(path, clocks)
    rewrite(path, TEXT.replace('bind = "172.20.10.5"', 'bind = "172.20.10.6"'))
    clocks.advance(5)
    block = watch.poll().status_block()
    assert block == {
        "sha256": sha(path),
        "loaded_at": "2026-10-25T10:15:05+03:00",
        "error": None,
        "restart_keys": ["collector"],
    }
    status = config_status_of({"listening": True, "config": block})
    assert status is not None
    assert (status.sha256, status.restart_keys) == (sha(path), ("collector",))
    assert config_status_of({"listening": True}) is None, "an older service: unknown"
    assert config_status_of({"config": {**block, "sha256": "zz"}}) is None
    assert config_status_of({"config": "text"}) is None


def test_hot_and_restart_keys_are_configuration_keys(path: Path, clocks: Clocks) -> None:
    assert COLLECTOR_HOT == INGEST_HOT == {"sources"}
    assert {"database", "worker", "data_dir", "secrets_dir"}.isdisjoint(WORKER_HOT)
    assert {"database", "api", "data_dir", "secrets_dir"}.isdisjoint(API_HOT)
    assert {"sources", "tsa", "smtp", "site_name", "worker"} <= API_HOT
    assert set(Config.model_fields) >= COLLECTOR_RESTART | INGEST_RESTART
    with pytest.raises(ValueError, match="distinct"):
        watcher(path, clocks, hot=frozenset({"sources", "nope"}))
    with pytest.raises(ValueError, match="distinct"):
        watcher(path, clocks, hot=frozenset({"sources"}), restart=frozenset({"sources"}))
    with pytest.raises(ValueError, match="not configuration keys"):
        configwatch.hot_keys_except("nope")
