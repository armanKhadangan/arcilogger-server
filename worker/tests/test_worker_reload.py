"""The worker takes a changed ``arcilogger.toml`` in ``tick()`` without a restart (ADR-0014,
contract 2.8) and reports the configuration in use in ``state/worker-status.json``."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from arcilogger_core.config import load_config
from arcilogger_core.configwatch import config_status_of
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_worker.service import Job, JobContext, Worker

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _write(path: Path, text: str, step: int) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = 1_800_000_000_000_000_000 + step * 1_000_000_000  # a new mtime for every write
    os.utime(path, ns=(stamp, stamp))


def _toml(data: Path, site: str, extra: str = "") -> str:
    return f'data_dir = "{data.as_posix()}"\nsite_name = "{site}"\n{extra}'


def _status(layout: Layout) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(layout.worker_status.read_text(encoding="utf-8"))
    return data


def test_the_worker_reloads_its_configuration_in_tick(db_engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "arcilogger.toml"
    data = tmp_path / "data"
    _write(path, _toml(data, "Örnek A.Ş."), 0)
    seen: list[tuple[str, datetime | None]] = []

    def probe(ctx: JobContext) -> Mapping[str, Any]:
        assert ctx.config is not None
        seen.append((ctx.config.site_name, ctx.replica_since))
        return {}

    clock = Clock()
    layout = Layout(data)
    worker = Worker(
        engine=db_engine,
        layout=layout,
        jobs=[Job("probe", probe, every=timedelta(seconds=1))],
        clock=clock,
        config=load_config(path),
        config_path=path,
        reload_seconds=0,
    )

    def tick() -> None:
        clock.now += timedelta(seconds=2)
        worker.tick()
        worker.write_status()

    def last() -> tuple[str, datetime | None]:
        """What the newest job run saw: the site name and ``replica_since``."""
        return seen[-1]

    tick()
    block = config_status_of(_status(layout))
    assert block is not None
    assert block.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (block.error, block.restart_keys) == (None, ())
    assert last() == ("Örnek A.Ş.", None)

    # A browser change of the site name reaches the next job, and the status names the new file.
    _write(path, _toml(data, "Örnek Holding A.Ş."), 1)
    tick()
    assert last() == ("Örnek Holding A.Ş.", None)
    block = config_status_of(_status(layout))
    assert block is not None
    assert block.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    # A file that does not load keeps the configuration in use and says why.
    good = block.sha256
    _write(path, _toml(data, "Bozuk") + "[smtp\n", 2)
    tick()
    assert last()[0] == "Örnek Holding A.Ş."
    block = config_status_of(_status(layout))
    assert block is not None
    assert (block.sha256, block.error is not None) == (good, True)

    # A key the worker reads only at start-up is reported, not taken.
    _write(path, _toml(data, "Örnek Holding A.Ş.", '[database]\nname = "arcilogger2"\n'), 3)
    tick()
    assert worker.config is not None
    assert worker.config.database.name == "arcilogger"
    block = config_status_of(_status(layout))
    assert block is not None
    assert block.restart_keys == ("database",)

    # A replica switched on now: its old days are counted from now (the R20 replica check).
    replica = f'[replica]\nenabled = true\ntarget = "{(tmp_path / "nas").as_posix()}"\n'
    _write(path, _toml(data, "Örnek Holding A.Ş.", replica), 4)
    tick()
    assert last() == ("Örnek Holding A.Ş.", clock.now)
    assert worker.replica_since == clock.now


def test_without_a_configuration_file_nothing_is_watched(db_engine: Engine, tmp_path: Path) -> None:
    layout = Layout(tmp_path / "data")
    worker = Worker(engine=db_engine, layout=layout, jobs=[], clock=Clock())
    assert (worker.admin, worker.config_watch) == (None, None)
    worker.tick()
    worker.write_status()
    assert "config" not in _status(layout)  # an older reader sees what it always saw
