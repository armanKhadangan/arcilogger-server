"""Rehearse the 0.5.0 upgrade on a large database (issue #297): time, index sizes, round trip.

An upgrade from 0.4.1 (head ``0023_usage_period``) to 0.5.0 (head ``0027_rule_traffic_totals``)
runs ``0024_rules`` … ``0027_rule_traffic_totals`` with the services stopped; ``0026`` builds
``(time_received)`` on ``fg_traffic``, ``fg_dns``, ``fg_web`` and ``fg_app`` over every row. This
script measures that on a database it builds itself, and drops the database at the end (unless
``--keep``):

1. **The database at ``0023``**: a new database on the server of ``--url``, migrated with the
   product's :func:`arcilogger_db.migrate.upgrade` to ``0023_usage_period`` (no seed: this
   version's seed is newer than that schema), with the daily partitions of the window (plain
   PostgreSQL). The rows are the load generator's (``tools/loadgen``, synthetic; ``--scenario
   all``) through the real parsers: one generated day is the template, and every site day of
   ``--days`` (the days before today, so that they fall into the partitions and, on TimescaleDB,
   behind the compression policy's 7 days) gets it ``--rows`` / ``--days`` rows strong in the
   four tables, written by the server (``INSERT … SELECT`` from the template; device times
   spread evenly over the day, receive times two seconds later, raw lines numbered per table and
   day). On TimescaleDB the compression policy jobs run (``run_job``) after each day, as the
   policy runs on a site day after day: the chunks older than 7 days are compressed, and only
   the last week's stay as they were written (``timescaledb_information.chunks`` is printed).
   Then ``ANALYZE``.
2. **The upgrade** to the head with :func:`arcilogger_db.migrate.upgrade` (what ``arcilogger-db
   upgrade`` runs: one transaction under the migrations' lock, then the seed and the partitions):
   each revision timed from alembic's ``Running upgrade`` log lines, the last one up to the
   seed. Then the size of the four new indexes, per table, with the chunks (TimescaleDB) or
   partitions that have one against those that do not.
3. **The round trip**: down to ``0023`` with :func:`arcilogger_db.downgrade.downgrade_schema`
   (``arcilogger-db schema downgrade``, the rollback's schema step), timed the same way, and up
   to the head again. Row count and checksum of each of the four tables (the sum of the first
   64 bits of the MD5 of every row's text: independent of the order of the rows, so of a
   compressed chunk's) before the first upgrade, after it, after the downgrade and after the
   second upgrade: they must be the same.
4. **The extrapolation**: seconds per 10 M rows of the four tables for every revision.

Usage::

    uv run --frozen python tools/bench/upgrade_rehearsal.py \\
        --url postgresql://USER:PASSWORD@127.0.0.1:5432/postgres [--timescale require] \\
        [--rows 50000000] [--days 14] [--out result.json]

The role needs ``CREATEDB`` (and on TimescaleDB the right to create the extension). Plain
PostgreSQL: ``--timescale off`` (the default); TimescaleDB: ``--timescale require`` on a server
with ``shared_preload_libraries = 'timescaledb'``. Numbers depend on the machine; the pull
request of #297 records the runs it quotes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import platform
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from sqlalchemy import Column, Connection, Engine, MetaData, Table, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.timeutil import SITE_TZ, site_day, utc_now
from arcilogger_db import migrate
from arcilogger_db.downgrade import downgrade_schema
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import Base
from arcilogger_db.partitions import ensure_partitions
from arcilogger_ingest.parsers import LinePosition, parse_line

#: The four tables ``0026_rules_time_received`` indexes.
TABLES: Final = ("fg_traffic", "fg_dns", "fg_web", "fg_app")
#: 0.4.1's head and 0.5.0's.
OLD_HEAD: Final = "0023_usage_period"
NEW_HEAD: Final = "0027_rule_traffic_totals"
LOADGEN: Final = Path(__file__).resolve().parents[1] / "loadgen" / "arcilogger_loadgen.py"
#: The generated template day (a Wednesday; only its rows are used, not its date).
TEMPLATE_DAY: Final = date(2026, 10, 14)
#: Receive time after device time, and the raw offset step of a written row.
RECEIVED_AFTER: Final = timedelta(seconds=2)
OFFSET_STEP: Final = 200
#: Device times of a day stay this far before its end, so that every receive time is that day's.
DAY_MARGIN: Final = timedelta(seconds=10)
PER_ROWS: Final = 10_000_000
#: How long :func:`drop_database` tries.
DROP_WAIT: Final = timedelta(minutes=10)
MIGRATION_LOGGER: Final = "alembic.runtime.migration"


def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen_rehearsal", LOADGEN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"the load generator is missing: {LOADGEN}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def template(users: int) -> dict[str, list[dict[str, Any]]]:
    """The parsed rows of the four tables of one generated day (every attack scenario on)."""
    lg = loadgen()
    settings = lg.Settings(users=users, scenarios=lg.parse_scenarios("all"))
    out = next(lg.Simulation(settings).run(TEMPLATE_DAY))
    sources = {source.name: source for source in settings.sources}
    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    for number, message in enumerate(out.messages, 1):
        source = sources[message.source]
        outcome = parse_line(
            RawRecord(message.received, source.address, message.wire),
            LinePosition(message.source, source.type, TEMPLATE_DAY, number, number * 100),
        )
        if outcome.error is not None:
            raise RuntimeError(f"the load generator's line does not parse: {outcome.error}")
        for row in outcome.rows:
            if row.table in rows:
                rows[row.table].append(dict(row.values))
    return rows


# -- the database at 0023 ------------------------------------------------------------------------


def create_database(server: str, name: str) -> str:
    """A new, empty database ``name`` on the server of ``server``; its URL."""
    admin = engine_from_url(server, application="rehearsal", isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            # As the installer creates it (Install-Arcilogger.ps1).
            connection.execute(
                text(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8' LOCALE 'C'")
            )
    finally:
        admin.dispose()
    return make_url(server).set(database=name).render_as_string(hide_password=False)


def drop_database(server: str, name: str) -> None:
    """Drops ``name``; while an autovacuum worker (which a role without superuser may not end)
    is still in it, tries again for up to :data:`DROP_WAIT`."""
    admin = engine_from_url(server, application="rehearsal", isolation_level="AUTOCOMMIT")
    until = time.monotonic() + DROP_WAIT.total_seconds()
    try:
        while True:
            try:
                with admin.connect() as connection:
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                return
            except DBAPIError:
                if time.monotonic() > until:
                    raise
                time.sleep(5)
    finally:
        admin.dispose()


def _template_tables(connection: Connection, rows: dict[str, list[dict[str, Any]]]) -> None:
    """``tpl_<table>``: the template's rows, ``id`` their ordinal from 0 (temporary tables)."""
    for table in TABLES:
        real = Base.metadata.tables[table]
        connection.execute(text(f"CREATE TEMPORARY TABLE tpl_{table} (LIKE {table})"))
        names = sorted({name for row in rows[table] for name in row} - {"id"})
        scratch = Table(
            f"tpl_{table}",
            MetaData(),
            Column("id", real.c.id.type),
            *(Column(name, real.c[name].type) for name in names),
        )
        values = [
            {"id": n, **{name: row.get(name) for name in names}}
            for n, row in enumerate(rows[table])
        ]
        connection.execute(insert(scratch), values)


def _fill_day(connection: Connection, table: str, day: date, copies: int, size: int) -> None:
    """``copies`` times the template of ``table`` into ``day``, device times spread evenly."""
    columns = list(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :table AND column_name <> 'id' ORDER BY ordinal_position"
            ),
            {"table": f"tpl_{table}"},
        ).scalars()
    )
    total = copies * size
    start = datetime.combine(day, datetime.min.time(), tzinfo=SITE_TZ)
    step = (timedelta(days=1) - DAY_MARGIN).total_seconds() / total
    position = "(k.k * :size + r.id)"
    device = f"CAST(:start AS timestamptz) + make_interval(secs => {position} * :step)"
    special = {
        "time": device,
        "time_received": f"{device} + make_interval(secs => :after)",
        "raw_day": "CAST(:day AS date)",
        "raw_line": f"{position} + 1",
        "raw_offset": f"({position} + 1) * :offset",
    }
    targets = ", ".join(columns)
    values = ", ".join(special.get(name, f"r.{name}") for name in columns)
    connection.execute(
        text(
            f"INSERT INTO {table} ({targets}) SELECT {values} "  # noqa: S608 - fixed text
            f"FROM tpl_{table} AS r CROSS JOIN generate_series(0, :copies - 1) AS k(k)"
        ),
        {
            "start": start,
            "step": step,
            "after": RECEIVED_AFTER.total_seconds(),
            "day": day,
            "size": size,
            "copies": copies,
            "offset": OFFSET_STEP,
        },
    )


def run_compression(engine: Engine) -> None:
    """TimescaleDB: the four tables' compression policy jobs, now (``run_job``)."""
    with engine.connect() as connection:
        jobs = connection.execute(
            text(
                "SELECT job_id FROM timescaledb_information.jobs "
                "WHERE proc_name = 'policy_compression' AND hypertable_name = ANY(:tables)"
            ),
            {"tables": list(TABLES)},
        ).scalars()
        for job in list(jobs):
            connection.execute(text("CALL run_job(:job)"), {"job": job})


def build(
    engine: Engine, rows: dict[str, list[dict[str, Any]]], target: int, days: int, mode: str
) -> dict[str, Any]:
    """The database at ``0023`` with about ``target`` rows in the four tables over ``days``."""
    migrate.upgrade(engine, revision=OLD_HEAD, timescale=mode, run_seed=False)  # type: ignore[arg-type]
    today = site_day(utc_now())
    first = today - timedelta(days=days)
    with engine.begin() as connection:
        ensure_partitions(connection, today=today, limit=10_000)
    template_size = sum(len(rows[table]) for table in TABLES)
    copies = max(1, math.ceil(target / days / template_size))
    autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
    compress = timescale(engine)
    written = compressed = 0.0
    with engine.connect() as connection:
        _template_tables(connection, rows)
        connection.commit()
        for offset in range(days):
            day = first + timedelta(days=offset)
            started = time.perf_counter()
            for table in TABLES:
                _fill_day(connection, table, day, copies, len(rows[table]))
                connection.commit()
            written += time.perf_counter() - started
            if compress:
                started = time.perf_counter()
                run_compression(autocommit)
                compressed += time.perf_counter() - started
            log(f"  {day}: written ({written:.0f} s, compression {compressed:.0f} s)")
    with autocommit.connect() as connection:
        for table in TABLES:
            connection.execute(text(f"ANALYZE {table}"))
    return {
        "days": [first.isoformat(), (today - timedelta(days=1)).isoformat()],
        "template_rows": {table: len(rows[table]) for table in TABLES},
        "copies_per_day": copies,
        "write_seconds": round(written, 1),
        "compression_seconds": round(compressed, 1),
    }


# -- what the database holds ---------------------------------------------------------------------


def timescale(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')")
            ).scalar_one()
        )


def fingerprint(engine: Engine) -> dict[str, list[int]]:
    """Per table: rows and the order-independent checksum of every row's text."""
    found: dict[str, list[int]] = {}
    with engine.connect() as connection:
        for table in TABLES:
            count, checksum = connection.execute(
                text(
                    "SELECT count(*), coalesce(sum(('x' || left(md5(CAST(t AS text)), 16))"  # noqa: S608 - fixed text
                    f"::bit(64)::bigint), 0) FROM {table} AS t"
                )
            ).one()
            found[table] = [int(count), int(checksum)]
    return found


def chunks(engine: Engine) -> dict[str, dict[str, int]]:
    """TimescaleDB: per table, its chunks and how many are compressed."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT hypertable_name, count(*), count(*) FILTER (WHERE is_compressed) "
                "FROM timescaledb_information.chunks WHERE hypertable_name = ANY(:tables) "
                "GROUP BY hypertable_name"
            ),
            {"tables": list(TABLES)},
        ).all()
    return {str(name): {"chunks": int(n), "compressed": int(c)} for name, n, c in rows}


def table_sizes(engine: Engine) -> dict[str, int]:
    """Per table, bytes on disk with its indexes (partitions, chunks and compressed data)."""
    sizes: dict[str, int] = {}
    with engine.connect() as connection:
        for table in TABLES:
            if timescale(engine):
                size = connection.execute(
                    text("SELECT hypertable_size(CAST(:table AS regclass))"), {"table": table}
                ).scalar_one()
            else:
                size = connection.execute(
                    text(
                        "SELECT sum(pg_total_relation_size(relid)) "
                        "FROM pg_partition_tree(CAST(:table AS regclass))"
                    ),
                    {"table": table},
                ).scalar_one()
            sizes[table] = int(size or 0)
    return sizes


def index_report(engine: Engine) -> dict[str, dict[str, int]]:
    """Per table: the bytes of ``ix_<table>_time_received`` over every partition or chunk, the
    partitions or chunks that have it and those that do not, and of the compressed chunks
    (TimescaleDB) how many have it and its bytes there (a compressed chunk's rows are in its
    compressed table, which the index does not cover)."""
    report: dict[str, dict[str, int]] = {}
    with engine.connect() as connection:
        for table in TABLES:
            index = f"ix_{table}_time_received"
            if timescale(engine):
                leaves = connection.execute(
                    text(
                        "SELECT CAST(format('%I.%I', chunk_schema, chunk_name) AS regclass), "
                        "is_compressed FROM timescaledb_information.chunks "
                        "WHERE hypertable_name = :table"
                    ),
                    {"table": table},
                ).all()
            else:
                leaves = connection.execute(
                    text(
                        "SELECT relid, false FROM pg_partition_tree(CAST(:table AS regclass)) "
                        "WHERE isleaf"
                    ),
                    {"table": table},
                ).all()
            entry = {
                "bytes": 0,
                "with": 0,
                "without": 0,
                "compressed_with": 0,
                "compressed_bytes": 0,
            }
            for relation, compressed in leaves:
                size = connection.execute(
                    text(
                        "SELECT sum(pg_relation_size(i.indexrelid)) FROM pg_index AS i "
                        "JOIN pg_attribute AS a ON a.attrelid = i.indrelid "
                        "AND a.attnum = i.indkey[0] "
                        "WHERE i.indrelid = CAST(:relation AS regclass) "
                        "AND a.attname = 'time_received' AND i.indnatts = 1"
                    ),
                    {"relation": str(relation)},
                ).scalar_one()
                if size is None:
                    entry["without"] += 1
                    continue
                entry["with"] += 1
                entry["bytes"] += int(size)
                if compressed:
                    entry["compressed_with"] += 1
                    entry["compressed_bytes"] += int(size)
            parent = connection.execute(
                text("SELECT to_regclass(:index) IS NOT NULL"), {"index": index}
            ).scalar_one()
            entry["on_parent"] = int(bool(parent))
            report[table] = entry
    return report


# -- timing the revisions ------------------------------------------------------------------------


class Steps(logging.Handler):
    """The start of every alembic step (``Running upgrade A -> B``) and a mark at its end."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.marks: list[tuple[float, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(("Running upgrade", "Running downgrade")):
            # "Running upgrade 0025_admin_webhook_test -> 0026_rules_time_received, ..."
            step = message.split(",", 1)[0].split(" ", 2)[2]
            self.marks.append((time.perf_counter(), step))

    def mark(self) -> None:
        self.marks.append((time.perf_counter(), ""))

    def durations(self) -> dict[str, float]:
        return {
            step: round(after - at, 2)
            for (at, step), (after, _) in zip(self.marks, self.marks[1:], strict=False)
            if step
        }


def timed(run: Callable[[Steps], Any]) -> tuple[dict[str, float], float]:
    """Runs ``run`` with alembic's steps recorded: each step's seconds, and the whole run's."""
    logger = logging.getLogger(MIGRATION_LOGGER)
    steps = Steps()
    level = logger.level
    logger.addHandler(steps)
    logger.setLevel(logging.INFO)
    started = time.perf_counter()
    try:
        run(steps)
    finally:
        logger.removeHandler(steps)
        logger.setLevel(level)
    return steps.durations(), round(time.perf_counter() - started, 2)


def upgrade(engine: Engine, mode: str) -> tuple[dict[str, float], float]:
    """``arcilogger-db upgrade``'s run; the last revision ends where the seed starts."""

    def run(steps: Steps) -> None:
        seed = migrate.seed

        def marked(*args: Any, **kwargs: Any) -> Any:
            steps.mark()
            return seed(*args, **kwargs)

        migrate.seed = marked  # type: ignore[assignment]
        try:
            migrate.upgrade(engine, timescale=mode)  # type: ignore[arg-type]
        finally:
            migrate.seed = seed  # type: ignore[assignment]

    return timed(run)


def downgrade(engine: Engine, mode: str) -> tuple[dict[str, float], float]:
    """``arcilogger-db schema downgrade --to 0023_usage_period``'s run."""

    def run(steps: Steps) -> None:
        with engine.connect() as connection:
            downgrade_schema(connection, OLD_HEAD, timescale=mode)  # type: ignore[arg-type]
        steps.mark()

    return timed(run)


def revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return ", ".join(migrate.current_revisions(connection))


def per_rows(seconds: dict[str, float], rows: int) -> dict[str, float]:
    return {step: round(value * PER_ROWS / rows, 2) for step, value in seconds.items()}


def log(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--url", required=True, help="a database on the server (role: CREATEDB)")
    parser.add_argument("--database", default="arci_rehearsal_297", help="created and dropped")
    parser.add_argument("--timescale", choices=("off", "require"), default="off")
    parser.add_argument("--rows", type=int, default=50_000_000, help="rows in the four tables")
    parser.add_argument("--days", type=int, default=14, help="site days before today")
    parser.add_argument("--users", type=int, default=120, help="people of the template day")
    parser.add_argument("--out", type=Path, help="the result as JSON")
    parser.add_argument("--keep", action="store_true", help="keep the database")
    args = parser.parse_args(argv)
    mode: str = args.timescale

    log("template day: generating and parsing")
    rows = template(args.users)
    url = create_database(args.url, args.database)
    engine = engine_from_url(url, application="rehearsal")
    result: dict[str, Any] = {
        "layout": "timescaledb" if mode == "require" else "postgresql",
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
    }
    try:
        log(f"building {args.database} at {OLD_HEAD}")
        result["build"] = build(engine, rows, args.rows, args.days, mode)
        with engine.connect() as connection:
            result["server"] = connection.execute(text("SELECT version()")).scalar_one()
            if timescale(engine):
                result["timescaledb"] = connection.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
                ).scalar_one()
        if timescale(engine):
            result["chunks"] = chunks(engine)
        result["sizes_before"] = table_sizes(engine)
        log(f"build: {json.dumps(result['build'])}")

        prints: dict[str, Any] = {"before": fingerprint(engine)}
        total = sum(count for count, _ in prints["before"].values())
        result["rows"] = {table: value[0] for table, value in prints["before"].items()}
        result["rows_total"] = total
        log(f"rows: {total:,} {json.dumps(result['rows'])}")

        for name, run in (
            ("upgrade", upgrade),
            ("downgrade", downgrade),
            ("upgrade_again", upgrade),
        ):
            log(f"{name} from {revision(engine)}")
            steps, whole = run(engine, mode)
            result[name] = {
                "to": revision(engine),
                "seconds": steps,
                "whole_seconds": whole,
                "seconds_per_10m_rows": per_rows(steps, total),
            }
            log(f"  {json.dumps(result[name])}")
            if name != "downgrade":
                result[f"indexes_after_{name}"] = index_report(engine)
                log(f"  indexes: {json.dumps(result[f'indexes_after_{name}'])}")
            prints[f"after_{name}"] = fingerprint(engine)
        if timescale(engine):
            result["chunks_after"] = chunks(engine)
        result["fingerprints"] = prints
        result["lossless"] = all(value == prints["before"] for value in prints.values())
        log(f"lossless: {result['lossless']}")
    finally:
        engine.dispose()
        # The result first: it outlives a database that cannot be dropped yet.
        text_out = json.dumps(result, indent=2, ensure_ascii=False)
        if args.out is not None:
            args.out.write_text(text_out + "\n", encoding="utf-8")
        log(text_out)
        if not args.keep:
            drop_database(args.url, args.database)
    return 0 if result["lossless"] else 1


if __name__ == "__main__":
    sys.exit(main())
