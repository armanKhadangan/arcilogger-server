"""Live reload of ``[[sources]]`` in the ingest service, with no restart (ADR-0014; contract
2.8): ``enabled`` reaches the ``sources`` table, open streams are resolved again, offsets stay
and every line is loaded exactly once."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from arcilogger_core.config import load_config
from arcilogger_db.engine import engine_from_url
from arcilogger_ingest.service import IngestService
from arcilogger_ingest.status import read_status
from arcilogger_ingest.testing import (
    DAY,
    SOURCES,
    TcpProxy,
    World,
    check_exactly_once,
    dhcp_message,
    fortigate_message,
    offsets,
)

SWITCH: dict[str, Any] = {"name": "switch-2", "type": "mikrotik", "address": "172.20.10.22"}


def toml_of(world: World, sources: Sequence[dict[str, Any]], extra: str = "") -> str:
    """``arcilogger.toml`` of the world (its folders, its quick ingest settings) with
    ``sources``."""
    lines = [
        f'data_dir = "{world.config.data_dir.as_posix()}"',
        f'secrets_dir = "{world.config.secrets_dir.as_posix()}"',
        "",
        "[ingest]",
        "batch_rows = 500",
        "batch_seconds = 0.05",
        "poll_seconds = 0.02",
    ]
    for source in sources:
        lines += ["", "[[sources]]"]
        for name, value in source.items():
            if isinstance(value, bool):
                lines.append(f"{name} = {str(value).lower()}")
            elif isinstance(value, str):
                lines.append(f'{name} = "{value}"')
            else:
                lines.append(f"{name} = {value}")
    return "\n".join(lines) + "\n" + extra


def rewrite(path: Path, content: str) -> None:
    """New content with a new modification time (as the agent's replace or an editor)."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_for(predicate: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


def lines_loaded(engine: Engine, source: str) -> int:
    """Lines of ``source`` on :data:`DAY` committed so far (0 before its first batch)."""
    return offsets(engine).get((source, DAY), (0, 0, False))[1]


def sources_table(engine: Engine) -> dict[str, tuple[str, bool, bool]]:
    """name -> (type, enabled, expected) of the ``sources`` table."""
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT name, type, enabled, expected FROM sources")).all()
    return {str(r[0]): (str(r[1]), bool(r[2]), bool(r[3])) for r in rows}


@pytest.fixture
def config_path(world: World) -> Path:
    path = world.root / "arcilogger.toml"
    rewrite(path, toml_of(world, SOURCES))
    return path


@contextmanager
def running(world: World, engine: Engine, config_path: Path) -> Iterator[IngestService]:
    """The service in a thread of its own, watching ``config_path`` every 50 ms."""
    stop = threading.Event()
    service = IngestService(
        load_config(config_path),
        engine,
        stop=stop,
        clock=world.clock,
        status_interval=0.05,
        backoff_max=0.2,
        config_path=config_path,
        reload_seconds=0.05,
    )
    runner = threading.Thread(target=service.run)
    runner.start()
    try:
        yield service
    finally:
        stop.set()
        runner.join(20)
    assert not runner.is_alive()


def status_config(world: World) -> dict[str, Any] | None:
    status = read_status(world.layout.ingest_status)
    block = status.get("config") if status else None
    return block if isinstance(block, dict) else None


def test_new_sources_are_taken_and_written_with_enabled(
    world: World, db_engine: Engine, config_path: Path
) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(30)))
    # A device sending before it is configured: its folder is read by content detection.
    world.append("switch-2", DAY, (dhcp_message(i) for i in range(10)), sender="172.20.10.22")
    with running(world, db_engine, config_path) as service:
        wait_for(lambda: lines_loaded(db_engine, "switch-2") == 10)
        assert service.streams[("switch-2", DAY)].info.source_type is None
        before = offsets(db_engine)
        rewrite(config_path, toml_of(world, [*SOURCES, {**SWITCH, "enabled": False}]))
        wait_for(lambda: sources_table(db_engine)["switch-2"][1] is False)
        assert sources_table(db_engine)["switch-2"] == ("mikrotik", False, True)
        stream = service.streams[("switch-2", DAY)]
        assert stream.info.source_type == "mikrotik"  # resolved again, offsets kept
        assert stream.source.config is not None
        assert (stream.offset, stream.line) == before[("switch-2", DAY)][:2]
        wait_for(lambda: (status_config(world) or {}).get("sha256") == sha(config_path))
        # A source switched off and on again, while lines keep arriving.
        world.append("fortigate", DAY, (fortigate_message(i) for i in range(30, 60)))
        world.append(
            "switch-2", DAY, (dhcp_message(i) for i in range(10, 20)), sender="172.20.10.22"
        )
        core_off = [{**source, "enabled": source["name"] != "core-r1"} for source in SOURCES]
        rewrite(config_path, toml_of(world, [*core_off, SWITCH]))
        wait_for(lambda: sources_table(db_engine)["core-r1"][1] is False)
        assert sources_table(db_engine)["switch-2"][1] is True
        wait_for(lambda: lines_loaded(db_engine, "fortigate") == 60)
        wait_for(lambda: lines_loaded(db_engine, "switch-2") == 20)
        rewrite(config_path, toml_of(world, [*SOURCES, SWITCH]))
        wait_for(lambda: sources_table(db_engine)["core-r1"][1] is True)
    assert check_exactly_once(db_engine, world) == []
    assert sources_table(db_engine)["fortigate"] == ("fortigate", True, True)


def test_read_but_unloaded_lines_are_read_again_with_the_new_sources(
    world: World, db_engine: Engine, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reload drops the batch being built (read for the old sources); its lines are read
    again and loaded once, prepared for the new sources."""

    def slow(text: str) -> str:
        # A batch that waits 5 s for more lines is still being built when the file changes
        # (with 50 ms a slow fill alone could make it due before the reload).
        return text.replace("batch_seconds = 0.05", "batch_seconds = 5")

    rewrite(config_path, slow(toml_of(world, SOURCES)))
    world.append("switch-2", DAY, (dhcp_message(i) for i in range(300)), sender="172.20.10.22")
    service = IngestService(
        load_config(config_path),
        db_engine,
        clock=world.clock,
        config_path=config_path,
        reload_seconds=0.0,
    )
    fill = service._fill
    reads: list[int] = []

    def fill_then_reconfigure() -> int:
        read = fill()
        if not reads:  # the batch holds the lines now, not loaded yet
            assert service._head.lines == 300
            rewrite(config_path, slow(toml_of(world, [*SOURCES, SWITCH])))
        reads.append(read)
        return read

    monkeypatch.setattr(service, "_fill", fill_then_reconfigure)
    assert service.run(once=True) == 0
    assert reads[:2] == [300, 300]  # read twice, loaded once
    assert check_exactly_once(db_engine, world) == []
    assert service.streams[("switch-2", DAY)].info.source_type == "mikrotik"
    with db_engine.connect() as connection:
        kinds = connection.execute(
            text("SELECT DISTINCT type FROM messages WHERE source = 'switch-2'")
        ).all()
    assert [row[0] for row in kinds] == ["dhcp"]  # parsed as MikroTik lines


@pytest.fixture
def proxied_engine(proxy: TcpProxy, pg_dsn: str) -> Iterator[Engine]:
    engine = engine_from_url(proxy.dsn(pg_dsn), application="ingest-test")
    yield engine
    engine.dispose()


def test_a_change_while_the_database_is_down_is_taken_and_synced_later(
    world: World, db_engine: Engine, proxy: TcpProxy, proxied_engine: Engine, config_path: Path
) -> None:
    """The batch retried during the outage does not hold the new sources back: the status
    names the new file at once (the browser does not end at ``partially_active``), and the
    ``sources`` table follows when the database is back."""
    world.append("switch-2", DAY, (dhcp_message(i) for i in range(10)), sender="172.20.10.22")
    with running(world, proxied_engine, config_path) as service:
        wait_for(lambda: lines_loaded(db_engine, "switch-2") == 10)
        proxy.down()
        world.append(
            "switch-2", DAY, (dhcp_message(i) for i in range(10, 20)), sender="172.20.10.22"
        )
        wait_for(lambda: proxy.refused >= 2)  # the new batch is being retried
        rewrite(config_path, toml_of(world, [*SOURCES, {**SWITCH, "enabled": False}]))
        wait_for(lambda: (status_config(world) or {}).get("sha256") == sha(config_path))
        status = read_status(world.layout.ingest_status)
        assert status is not None
        assert status["database"]["ok"] is False
        assert service.streams[("switch-2", DAY)].info.source_type == "mikrotik"
        assert sources_table(db_engine)["switch-2"][1] is True  # not written yet
        proxy.up()
        wait_for(lambda: sources_table(db_engine)["switch-2"] == ("mikrotik", False, True))
        wait_for(lambda: lines_loaded(db_engine, "switch-2") == 20)
    assert check_exactly_once(db_engine, world) == []


def test_a_broken_file_keeps_the_sources_and_loading_goes_on(
    world: World, db_engine: Engine, config_path: Path
) -> None:
    good_sha = sha(config_path)
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10)))
    with running(world, db_engine, config_path) as service:
        wait_for(lambda: (status_config(world) or {}).get("sha256") == good_sha)
        rewrite(config_path, toml_of(world, SOURCES).replace('type = "mikrotik"', 'type = "x"'))
        wait_for(lambda: (status_config(world) or {}).get("error") is not None)
        block = status_config(world)
        assert block is not None
        assert block["sha256"] == good_sha  # still the file the sources came from
        assert block["error"].startswith("sources.1.type: ")
        assert [source.name for source in service.config.sources] == ["fortigate", "core-r1", "ap"]
        world.append("core-r1", DAY, (dhcp_message(i) for i in range(10, 20)))
        wait_for(lambda: lines_loaded(db_engine, "core-r1") == 20)
    assert check_exactly_once(db_engine, world) == []


def test_a_start_up_key_is_reported_not_taken(
    world: World, db_engine: Engine, config_path: Path
) -> None:
    with running(world, db_engine, config_path) as service:
        wait_for(lambda: status_config(world) is not None)
        rewrite(config_path, toml_of(world, SOURCES).replace("batch_rows = 500", "batch_rows = 7"))
        wait_for(lambda: (status_config(world) or {}).get("restart_keys") == ["ingest"])
        assert service.config.ingest.batch_rows == 500
        assert (status_config(world) or {})["sha256"] == sha(config_path)


def test_without_a_watched_file_there_is_no_config_block(world: World, db_engine: Engine) -> None:
    service = world.service(db_engine)
    assert service.run(once=True) == 0
    status = read_status(world.layout.ingest_status)
    assert status is not None
    assert "config" not in status
