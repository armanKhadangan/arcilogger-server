"""End-to-end ingest throughput: realistic spool files → PostgreSQL (not collected by pytest).

Backlog mode (how fast a backlog is loaded, ``run --once``)::

    ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres \\
        uv run python ingest/tests/bench_throughput.py --lines 200000 --batch-rows 5000 \\
        --workers 0 2

Live mode (lines appended at a fixed rate while the service runs normally; prints the largest
backlog and how long the service needs to catch up when the feed stops)::

    ... bench_throughput.py --live-rate 10000 --seconds 30 --workers 0 2

Each run uses a throwaway database (migrated + seeded, like the tests) and lines written with
``rawlog.format_line`` exactly as the collector does: 70 % FortiGate traffic lines of the format
samples, 30 % synthetic MikroTik DHCP lines. Results are reported in docs/INGEST.md.

A virtual machine's speed varies by about 20 % from run to run: report ``--repeat 3`` (or more)
with the printed minimum, median and load average, never a single fast run.
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy.exc import DBAPIError

from arcilogger_db.engine import engine_from_url
from arcilogger_db.testing import TestServer, admin_dsn_from_env
from arcilogger_ingest.testing import World, measure_live, measure_throughput


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lines", type=int, default=200_000)
    parser.add_argument("--batch-rows", type=int, nargs="+", default=[5000])
    parser.add_argument("--batch-seconds", type=float, default=0.5)
    parser.add_argument("--workers", type=int, nargs="+", default=[0])
    parser.add_argument("--live-rate", type=int, default=0, help="lines/s (0: backlog mode)")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--repeat", type=int, default=1, help="runs per configuration")
    args = parser.parse_args()
    dsn = admin_dsn_from_env()
    if dsn is None:
        print("set ARCILOGGER_TEST_PG", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING)
    server = TestServer(dsn)
    template = server.create_template(timescale="auto")
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
                        result = measure_throughput(
                            world, engine, lines=args.lines, workers=workers
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
