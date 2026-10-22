"""Bad input and unlucky shapes never stop or slow down ingest.

* damaged spool lines (a sender that is not ASCII, a receive time without a UTC datetime) and
  parser exceptions cost one line each, in the service process and in parse workers;
* the sources of one day share each batch (a backlog of one does not hold back the others);
* rows of one table that leave out different columns still load with one ``COPY`` per column
  set and get their server defaults, and a ``COPY`` that is refused for the batch as a whole is
  reported.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import time
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DataError

from arcilogger_core.rawlog import RawRecord, format_line
from arcilogger_core.timeutil import to_utc
from arcilogger_db.bulk import copy_rows
from arcilogger_ingest import loader, prepare
from arcilogger_ingest.cli import EXIT_OK, main
from arcilogger_ingest.loader import Batch, LoadResult
from arcilogger_ingest.parsers import LinePosition, ParsedRow, ParseOutcome, get_parser
from arcilogger_ingest.prepare import PATTERN_EXCEPTION, PATTERN_RAW
from arcilogger_ingest.service import IngestService
from arcilogger_ingest.testing import (
    DAY,
    World,
    dhcp_message,
    fortigate_message,
    loaded,
    offsets,
    write_config_file,
)

#: Damaged lines as file-system damage could leave them (not via format_line).
DAMAGED = (
    b"2026-09-23T09:44:57.123456+03:00 \xff\xfe <13>damaged sender\n",
    b"0001-01-01T00:00:00+03:00 172.20.10.1 <13>year one\n",
    b"9999-12-31T23:59:59-03:00 172.20.10.1 <13>year ten thousand\n",
)


def query(engine: Engine, sql: str) -> list[Any]:
    with engine.connect() as connection:
        return list(connection.execute(text(sql)).all())


def write_damaged_day(world: World) -> None:
    """core-r1: 10 good lines, the damaged ones, 10 more; fortigate: 20 good lines."""
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10)))
    world.append_raw("core-r1", DAY, b"".join(DAMAGED))
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10, 20)), start=time(9))
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(20)))


def assert_damaged_day_loaded(world: World, engine: Engine) -> None:
    good = {(s, d, n, o) for s, d, n, o in world.expected() if not 11 <= n <= 13 or s != "core-r1"}
    assert set(loaded(engine)) == good
    assert len(loaded(engine)) == 40
    failures = query(engine, "SELECT source, pattern, count, example_line FROM parse_failures")
    assert failures == [("core-r1", PATTERN_RAW, 3, 11)]
    path = world.layout.spool_file("core-r1", DAY)
    assert offsets(engine)[("core-r1", DAY)] == (path.stat().st_size, 23, False)


@pytest.mark.parametrize("workers", [0, 1])
def test_damaged_lines_cost_one_line_each(world: World, db_engine: Engine, workers: int) -> None:
    """Regression: these lines crashed the service (exit 1) on every restart, for every source."""
    write_damaged_day(world)
    service = world.service(db_engine, workers=workers)
    assert service.run(once=True) == EXIT_OK
    assert_damaged_day_loaded(world, db_engine)
    assert world.service(db_engine).run(once=True) == EXIT_OK  # and nothing twice on a re-run
    assert len(loaded(db_engine)) == 40


def test_damaged_lines_through_the_command_line(
    world: World, db_engine: Engine, pg_dsn: str
) -> None:
    write_damaged_day(world)
    config = write_config_file(world, pg_dsn)
    assert main(["run", "--config", str(config), "--once", "--workers", "1"]) == EXIT_OK
    assert_damaged_day_loaded(world, db_engine)


def test_a_parser_exception_is_counted_logged_once_and_the_message_kept(
    world: World,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode_on_odd_lines(record: RawRecord, position: LinePosition) -> ParseOutcome:
        if position.raw_line % 2:
            raise OverflowError("date value out of range")
        return get_parser("generic").parse(record, position)

    monkeypatch.setattr(prepare, "parse_line", explode_on_odd_lines)
    world.reconfigure(ingest={"batch_rows": 4})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(12)))
    assert world.service(db_engine).run(once=True) == EXIT_OK
    assert len(loaded(db_engine)) == 12
    flagged = query(db_engine, "SELECT raw_line FROM messages WHERE parse_error IS NOT NULL")
    assert sorted(r[0] for r in flagged) == [1, 3, 5, 7, 9, 11]
    failures = query(db_engine, "SELECT pattern, count, example_line, error FROM parse_failures")
    assert failures == [(PATTERN_EXCEPTION, 6, 1, "OverflowError: date value out of range")]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1  # once per stream, although three batches had such lines
    assert "core-r1 2026-09-23 line 1" in errors[0].getMessage()


def test_the_sources_of_a_day_share_every_batch(world: World, db_engine: Engine) -> None:
    """A large DHCP backlog must not hold FortiGate back (it sorts after ``core-r1``)."""
    world.reconfigure(ingest={"batch_rows": 200})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(3000)))
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(500)))
    world.append("unknown-10.9.9.9", DAY, [b"<13>a few"] * 30, sender="10.9.9.9")
    world.append("fortigate", DAY.replace(day=22), (fortigate_message(i) for i in range(50)))
    service = world.service(db_engine)
    batches: list[dict[str, int]] = []
    real_load = service.loader.load

    def recording(batch: Batch) -> LoadResult:
        batches.append(
            {
                f"{u.source}/{u.day.day}": u.new_line - u.old_line
                for u in batch.offsets.values()
                if u.new_line > u.old_line
            }
        )
        return real_load(batch)

    service.loader.load = recording  # type: ignore[method-assign]
    assert service.run(once=True) == EXIT_OK
    assert len(loaded(db_engine)) == 3580
    first, second = batches[0], batches[1]
    # equal shares (67 each), the few unknown lines, then the rest of the room shared again
    assert set(first) == {"core-r1/23", "fortigate/23", "unknown-10.9.9.9/23"}
    assert first["unknown-10.9.9.9/23"] == 30
    assert sum(first.values()) == 200
    assert abs(first["core-r1/23"] - first["fortigate/23"]) <= 10
    assert set(second) == {"core-r1/23", "fortigate/23"}
    assert abs(second["core-r1/23"] - second["fortigate/23"]) <= 10
    fortigate_done = next(i for i, b in enumerate(batches) if "fortigate/23" not in b)
    assert fortigate_done <= 7  # 500 lines at about 100 a batch, not after the 3000 DHCP lines
    assert all("fortigate/22" not in b for b in batches[:-1])  # older days after the newest


def test_rows_leaving_out_defaulted_columns_load_without_bisection(
    world: World,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Some ``fg_traffic`` rows set ``is_interim``/``extra``, others leave them to the default."""

    def mixed(record: RawRecord, position: LinePosition) -> ParseOutcome:
        outcome = get_parser("generic").parse(record, position)
        values: dict[str, Any] = {
            "time": to_utc(record.received),
            "time_received": to_utc(record.received),
            "source": position.source,
            "sender": record.sender,
            "raw_day": position.raw_day,
            "raw_line": position.raw_line,
            "raw_offset": position.raw_offset,
        }
        if position.raw_line % 2 == 0:
            values.update(is_interim=True, extra={"even": True})
        return ParseOutcome(
            parser="fortigate", message=outcome.message, rows=(ParsedRow("fg_traffic", values),)
        )

    copies: list[tuple[str, int]] = []

    def counting_copy(
        connection: Connection,
        table: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        columns: Sequence[str] | None = None,
    ) -> int:
        count = copy_rows(connection, table, rows, columns=columns)
        copies.append((table, count))
        return count

    monkeypatch.setattr(prepare, "parse_line", mixed)
    monkeypatch.setattr(loader, "copy_rows", counting_copy)
    caplog.set_level("DEBUG", logger="arcilogger.ingest")
    world.reconfigure(ingest={"batch_rows": 200})
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(400)))
    assert world.service(db_engine).run(once=True) == EXIT_OK
    assert "COPY rejected" not in caplog.text
    # per batch: one COPY for messages, one per fg_traffic column set
    assert sorted(copies) == [("fg_traffic", 100)] * 4 + [("messages", 200)] * 2
    rows = query(
        db_engine,
        "SELECT raw_line % 2, is_interim, extra::text, count(*) FROM fg_traffic "
        "GROUP BY 1, 2, 3 ORDER BY 1",
    )
    assert rows == [(0, True, '{"even": true}', 200), (1, False, "{}", 200)]


def test_a_batch_refused_as_a_whole_is_reported(
    world: World,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every line loads on its own, but COPY refused the batch: slow, so it must not be silent."""
    refused: list[int] = []

    def refuse_large_copies_once(
        connection: Connection,
        table: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        columns: Sequence[str] | None = None,
    ) -> int:
        rows = list(rows)
        if len(rows) > 50 and not refused:
            refused.append(len(rows))
            raise DataError("COPY", None, Exception("refused as a whole"))
        return copy_rows(connection, table, rows, columns=columns)

    monkeypatch.setattr(loader, "copy_rows", refuse_large_copies_once)
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(100)))
    assert world.service(db_engine).run(once=True) == EXIT_OK
    assert len(loaded(db_engine)) == 100
    assert query(db_engine, "SELECT count(*) FROM parse_failures") == [(0,)]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("COPY refused a batch of 100 lines 1 time(s)" in m for m in warnings), warnings


def test_a_file_of_mostly_damaged_lines(world: World, db_engine: Engine) -> None:
    """Damaged lines are counted as failures, the one good line after them still loads."""
    path = world.layout.spool_file("core-r1", DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(DAMAGED[0] * 3 + format_line(to_utc(world.now), "172.20.10.1", b"<13>ok"))
    service: IngestService = world.service(db_engine)
    assert service.run(once=True) == EXIT_OK
    assert service.counters.failures == 3
    assert len(loaded(db_engine)) == 1
