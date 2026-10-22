"""Throughput guard: realistic lines through the whole service (spool → PostgreSQL).

The real measurement is ``ingest/tests/bench_throughput.py`` (numbers in docs/INGEST.md). This
test only catches an order-of-magnitude regression, with a floor low enough for shared CI
runners, and prints the measured value.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from arcilogger_ingest.testing import World, measure_throughput

pytestmark = pytest.mark.perf

LINES = 20_000
FLOOR_LINES_PER_SECOND = 2_000


def every_line_once(engine: Engine) -> dict[str, int]:
    """Lines per source, after checking they are exactly 1..n without duplicates."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT source, count(*), count(DISTINCT raw_line), min(raw_line), "
                "max(raw_line), count(DISTINCT raw_offset) FROM messages GROUP BY source"
            )
        ).all()
    result = {}
    for source, count, distinct, first, last, offsets in rows:
        assert count == distinct == last == offsets, source
        assert first == 1
        result[str(source)] = int(count)
    return result


def test_sustained_throughput_floor(
    world: World, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    world.reconfigure(ingest={"batch_rows": 5000, "batch_seconds": 0.5, "poll_seconds": 0.5})
    result = measure_throughput(world, db_engine, lines=LINES)
    with capsys.disabled():
        print(f"\ningest throughput: {result.summary()}")
    assert result.lines == LINES
    # every line gives its messages row plus exactly one typed row (fg_*/mt_dhcp)
    assert result.rows == 2 * LINES
    assert result.lines_per_second >= FLOOR_LINES_PER_SECOND, result.summary()
    assert every_line_once(db_engine) == {"fortigate": 14_000, "core-r1": 6_000}


def test_throughput_with_parse_workers(world: World, db_engine: Engine) -> None:
    world.reconfigure(ingest={"batch_rows": 1000})
    result = measure_throughput(world, db_engine, lines=6_000, workers=2)
    assert result.lines == 6_000
    assert result.bytes > 6_000 * 100
    assert every_line_once(db_engine) == {"fortigate": 4_200, "core-r1": 1_800}
    assert "lines/s" in result.summary()
