"""End-to-end ingest throughput: realistic spool files → PostgreSQL (not collected by pytest).

Backlog mode (how fast a backlog is loaded, ``run --once``)::

    ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres \\
        uv run python ingest/tests/bench_throughput.py --lines 200000 --batch-rows 5000 \\
        --workers 0 2

Live mode (lines appended at a fixed rate while the service runs normally; prints the largest
backlog and how long the service needs to catch up when the feed stops)::

    ... bench_throughput.py --live-rate 10000 --seconds 30 --workers 0 2

Each run uses a throwaway database (migrated + seeded, like the tests) and lines written with
``rawlog.format_line`` exactly as the collector does. ``--mix default``: 70 % FortiGate traffic
lines of the format samples, 30 % synthetic MikroTik DHCP lines; ``--mix fortigate-only``
(ADR-0016): 70 % forward traffic lines with ``srcmac``, 30 % synthetic FortiGate DHCP server
lines. Results are reported in docs/INGEST.md.

**One spool, two trees** (the Wave 3 gate, docs/INGEST.md): ``--write-spool DIR`` writes
``--lines`` lines of ``--mix`` into ``DIR`` once (``DIR/<source>/<day>.log``, the spool
layout) and exits; ``--spool DIR`` then loads a copy of exactly those files in every run. The
spool mode uses only what both trees have (``World``, ``World.service``, ``Throughput``), so
this file runs unchanged against an older tree (``PYTHONPATH`` set to its ``core/src``,
``db/src`` and ``ingest/src``), which stores the same lines in its own tables.

A virtual machine's speed varies by about 20 % from run to run: report ``--repeat 3`` (or more)
with the printed minimum, median and load average, never a single fast run.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

from arcilogger_db.engine import engine_from_url
from arcilogger_db.testing import TestServer, admin_dsn_from_env
from arcilogger_ingest.testing import (
    DAY,
    Throughput,
    World,
    _machine_cpu,
    measure_live,
    measure_throughput,
)


def _drop(server: TestServer, name: str) -> None:
    """After a big load an autovacuum worker may hold the database; the (non-superuser) test
    role cannot terminate it, so ``DROP … WITH (FORCE)`` is retried until autovacuum is done."""
    for attempt in range(60):
        try:
            server.drop_database(name)
            return
        except DBAPIError:
            if attempt == 59:
                raise
            time.sleep(1)


def _spool_files(spool: Path) -> list[Path]:
    """The day files of a spool folder written by ``--write-spool`` (``<source>/<day>.log``)."""
    return sorted(path for path in spool.glob("*/*") if path.is_file())


def write_spool(spool: Path, *, lines: int, mix: str) -> int:
    """``--write-spool``: ``lines`` lines of ``mix`` into the new folder ``spool``."""
    from arcilogger_ingest.testing import write_realistic_spool  # its mix: this tree only

    with tempfile.TemporaryDirectory() as folder:
        world = World.create(Path(folder))
        size = write_realistic_spool(world, DAY, lines, mix=mix)
        shutil.copytree(world.layout.spool, spool)
    return size


def measure_spool(world: World, engine: Engine, spool: Path, *, workers: int) -> Throughput:
    """Copy the files of ``spool`` into ``world``'s spool, then time ``run(once=True)``."""
    size = 0
    for path in _spool_files(spool):
        target = world.layout.spool / path.parent.name / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        size += target.stat().st_size
    service = world.service(engine, status_interval=5.0, workers=workers)
    cpu_before, machine_before = time.process_time(), _machine_cpu()
    started = time.perf_counter()
    if service.run(once=True) != 0:
        raise AssertionError("ingest did not finish")
    seconds = time.perf_counter() - started
    cpu = time.process_time() - cpu_before
    machine_after = _machine_cpu()
    machine = None
    if machine_before is not None and machine_after is not None:
        busy = machine_after[0] - machine_before[0]
        total = machine_after[1] - machine_before[1]
        machine = 100.0 * busy / total if total else None
    return Throughput(
        lines=service.counters.lines,
        rows=service.counters.rows,
        bytes=size,
        seconds=seconds,
        process_cpu_seconds=cpu,
        machine_cpu_percent=machine,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lines", type=int, default=200_000)
    parser.add_argument("--batch-rows", type=int, nargs="+", default=[5000])
    parser.add_argument("--batch-seconds", type=float, default=0.5)
    parser.add_argument("--workers", type=int, nargs="+", default=[0])
    parser.add_argument("--live-rate", type=int, default=0, help="lines/s (0: backlog mode)")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--repeat", type=int, default=1, help="runs per configuration")
    parser.add_argument(
        "--mix", default="default", help="default or fortigate-only (written lines)"
    )
    parser.add_argument(
        "--write-spool", type=Path, help="write --lines lines of --mix into this new folder"
    )
    parser.add_argument("--spool", type=Path, help="load a copy of this --write-spool folder")
    args = parser.parse_args()
    if args.write_spool is not None:
        size = write_spool(args.write_spool, lines=args.lines, mix=args.mix)
        print(f"{args.lines} {args.mix} lines ({size / 1e6:.1f} MB) in {args.write_spool}")
        return 0
    if args.spool is not None and (args.live_rate or not _spool_files(args.spool)):
        print(f"--spool: no spool files in {args.spool}, or --live-rate given", file=sys.stderr)
        return 2
    dsn = admin_dsn_from_env()
    if dsn is None:
        print("set ARCILOGGER_TEST_PG", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING)
    server = TestServer(dsn)
    template = server.create_template(timescale="auto")
    # Only a mix other than the default is passed on: an older tree's helpers have no ``mix``.
    mix = {"mix": args.mix} if args.mix != "default" else {}
    try:
        runs = [
            (rows, workers)
            for rows in args.batch_rows
            for workers in args.workers
            for _ in range(max(1, args.repeat))
        ]
        rates: dict[tuple[int, int], list[float]] = {}
        for batch_rows, workers in runs:
            name = server.new_name("t")
            server.create_database(name, template=template)
            with tempfile.TemporaryDirectory() as folder:
                engine = engine_from_url(server.dsn_for(name), application="bench")
                try:
                    world = World.create(
                        Path(folder),
                        ingest={
                            "batch_rows": batch_rows,
                            "batch_seconds": args.batch_seconds,
                            "poll_seconds": 0.5,
                        },
                    )
                    if args.live_rate:
                        summary = measure_live(
                            world,
                            engine,
                            rate=args.live_rate,
                            seconds=args.seconds,
                            workers=workers,
                        ).summary()
                    else:
                        if args.spool is not None:
                            result = measure_spool(world, engine, args.spool, workers=workers)
                        else:
                            result = measure_throughput(
                                world, engine, lines=args.lines, workers=workers, **mix
                            )
                        rates.setdefault((batch_rows, workers), []).append(result.lines_per_second)
                        summary = result.summary()
                finally:
                    engine.dispose()
                    _drop(server, name)
            print(f"batch_rows={batch_rows} workers={workers}: {summary}", flush=True)
        for (batch_rows, workers), values in rates.items():
            print(
                f"batch_rows={batch_rows} workers={workers}: {len(values)} runs, min "
                f"{min(values):,.0f} / median {statistics.median(values):,.0f} lines/s",
                flush=True,
            )
        if hasattr(os, "getloadavg"):
            print("load average (1/5/15 min): {:.1f} {:.1f} {:.1f}".format(*os.getloadavg()))
    finally:
        server.drop_database(template)
        server.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
