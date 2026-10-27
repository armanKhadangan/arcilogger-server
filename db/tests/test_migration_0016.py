"""Revision ``0016_search`` (contract 2.8, 3.3): up and down on native partitions and on
TimescaleDB with compressed chunks, ``counts_from_line`` after the upgrade, the summary table's
key and the saved searches' rules."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, upgrade
from arcilogger_db.models import SavedSearch, User
from arcilogger_db.testing import TestServer, pause_compression_jobs

BEFORE = "0015_admin_requests"
REVISION = "0016_search"
NEW_TABLES = {"search_counts", "saved_searches"}


@pytest.fixture
def empty_engine(pg_server: TestServer) -> Iterator[Engine]:
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _timescale(engine: Engine) -> bool:
    available = _scalar(engine, "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    return available is not None


def _old_data(engine: Engine, days_ago: int) -> datetime:
    """Rows written before the upgrade: two files' offsets and web filter rows ``days_ago``."""
    at = (datetime.now(UTC) - timedelta(days=days_ago)).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ingest_offsets (source, day, byte_offset, line_number) VALUES "
                "('fortigate', :day, 4096, 10), ('core-r1', :day, 0, 0)"
            ),
            {"day": at.date()},
        )
        for number, host in enumerate(("www.example.com", "Example.COM", "my-example.com"), 1):
            connection.execute(
                text(
                    "INSERT INTO fg_web (time, time_received, source, sender, raw_day, "
                    "raw_line, raw_offset, hostname, action) VALUES (:time, :time, "
                    "'fortigate', '172.31.255.1', :day, :line, :offset, :host, 'blocked')"
                ),
                {
                    "time": at + timedelta(seconds=number),
                    "day": at.date(),
                    "line": number,
                    "offset": 100 * number,
                    "host": host,
                },
            )
    return at


def _domain_rows(engine: Engine) -> list[int]:
    """``domain:example.com`` on ``fg_web`` as the search compiles it (contract 2.5.4)."""
    with engine.connect() as connection:
        connection.execute(text("SET enable_seqscan = off"))
        return list(
            connection.execute(
                text(
                    "SELECT raw_line FROM fg_web WHERE reverse(lower(hostname)) = :r "
                    "OR (reverse(lower(hostname)) >= :lo AND reverse(lower(hostname)) < :hi) "
                    "ORDER BY raw_line"
                ),
                {"r": "moc.elpmaxe", "lo": "moc.elpmaxe.", "hi": "moc.elpmaxe/"},
            ).scalars()
        )


def _uses_the_index(engine: Engine) -> bool:
    with engine.connect() as connection:
        connection.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            connection.execute(
                text("EXPLAIN SELECT 1 FROM fg_web WHERE reverse(lower(hostname)) = 'x'")
            ).scalars()
        )
    return "rhost" in plan or "reverse" in plan.split("Filter")[0]


def _check_upgraded(engine: Engine, day: date, *, plan: bool = True) -> None:
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= NEW_TABLES
    with engine.connect() as connection:
        offsets: dict[str, int | None] = dict(
            connection.execute(
                text("SELECT source, counts_from_line FROM ingest_offsets WHERE day = :day"),
                {"day": day},
            ).all()
        )
        nulls = connection.execute(
            text(
                "SELECT indnullsnotdistinct FROM pg_index WHERE indexrelid = "
                "CAST('uq_search_counts_key' AS regclass)"
            )
        ).scalar_one()
    assert offsets == {"fortigate": 11, "core-r1": None}
    assert nulls is True
    assert "ix_fg_web_rhost_time" in {index["name"] for index in inspector.get_indexes("fg_web")}
    assert _domain_rows(engine) == [1, 2]  # not my-example.com
    if plan:
        assert _uses_the_index(engine)


def _check_downgraded(engine: Engine, rows: int = 3) -> None:
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    inspector = inspect(engine)
    assert not NEW_TABLES & set(inspector.get_table_names())
    assert "counts_from_line" not in {c["name"] for c in inspector.get_columns("ingest_offsets")}
    assert "ix_fg_web_rhost_time" not in {i["name"] for i in inspector.get_indexes("fg_web")}
    assert _scalar(engine, "SELECT count(*) FROM fg_web") == rows  # the rows stay
    assert upgrade(engine).after == (REVISION,)  # and up again


def test_up_and_down_on_native_partitions(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    assert not NEW_TABLES & set(inspect(empty_engine).get_table_names())
    old = _old_data(empty_engine, 20)
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)
    _check_upgraded(empty_engine, old.date())
    partitions = _scalar(
        empty_engine,
        "SELECT count(*) FROM pg_inherits i JOIN pg_index x ON x.indrelid = i.inhrelid "
        "JOIN pg_class c ON c.oid = x.indexrelid "
        "WHERE i.inhparent = CAST('fg_web' AS regclass) "
        "AND pg_get_indexdef(x.indexrelid) LIKE '%reverse(lower(hostname))%'",
    )
    assert partitions == _scalar(
        empty_engine,
        "SELECT count(*) FROM pg_inherits WHERE inhparent = CAST('fg_web' AS regclass)",
    )  # every partition, the default one included
    _check_downgraded(empty_engine)


def test_up_and_down_on_timescaledb_with_compressed_chunks(empty_engine: Engine) -> None:
    """The one place that shows the plain ``CREATE INDEX`` is accepted with compressed chunks
    present: on the Linux CI job (``ARCILOGGER_EXPECT_TIMESCALE=1``) it fails instead of
    skipping."""
    expected = os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1"
    if not _timescale(empty_engine):
        if expected:
            pytest.fail("TimescaleDB expected but not installed")
        pytest.skip("TimescaleDB is not installed (the Linux CI job runs this)")
    upgrade(empty_engine, revision=BEFORE, timescale="require")
    status = _scalar(empty_engine, "SELECT value FROM settings WHERE key = 'db.timescale'")
    if not status.get("compression"):
        if expected:
            pytest.fail(f"TimescaleDB expected with compression: {status}")
        pytest.skip(f"no compression in this TimescaleDB edition: {status}")
    pause_compression_jobs(empty_engine)
    old = _old_data(empty_engine, 30)
    with empty_engine.begin() as connection:
        compressed = connection.execute(
            text(
                "SELECT count(compress_chunk(chunk, if_not_compressed => true)) "
                "FROM show_chunks('fg_web', older_than => INTERVAL '7 days') AS chunk"
            )
        ).scalar_one()
    assert compressed >= 1
    assert upgrade(empty_engine).after == (REVISION,)
    _check_upgraded(empty_engine, old.date(), plan=False)  # one chunk, compressed
    chunks = _scalar(
        empty_engine,
        "SELECT count(*) FROM timescaledb_information.chunks c WHERE c.hypertable_name = 'fg_web' "
        "AND NOT EXISTS (SELECT 1 FROM pg_indexes i WHERE i.schemaname = c.chunk_schema "
        "AND i.tablename = c.chunk_name AND i.indexdef LIKE '%reverse(lower(hostname))%')",
    )
    assert chunks == 0  # every chunk has the index, the compressed one included
    with empty_engine.begin() as connection:  # a new chunk gets it from the hypertable
        connection.execute(
            text(
                "INSERT INTO fg_web (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, hostname) VALUES (now(), now(), 'fortigate', '172.31.255.1', "
                "current_date, 1, 0, 'mail.example.com')"
            )
        )
    assert _uses_the_index(empty_engine)
    still = _scalar(
        empty_engine,
        "SELECT count(*) FROM timescaledb_information.chunks "
        "WHERE hypertable_name = 'fg_web' AND is_compressed",
    )
    assert still >= 1  # the index was built with the compressed chunks in place
    _check_downgraded(empty_engine, rows=4)  # the three old rows and the new chunk's


# -- saved searches -------------------------------------------------------------------------------


def _user(session: Session, name: str) -> User:
    user = User(username=name, display_name=name.title(), role="auditor", password_hash="!")
    session.add(user)
    session.flush()
    return user


def _refused(session: Session, row: SavedSearch, constraint: str) -> None:
    savepoint = session.begin_nested()
    session.add(row)
    with pytest.raises(IntegrityError, match=constraint):
        session.flush()
    savepoint.rollback()


def test_saved_search_rules(db_session: Session) -> None:
    owner, other = _user(db_session, "ayse"), _user(db_session, "mehmet")
    start = datetime(2026, 10, 1, tzinfo=UTC)

    def search(**values: Any) -> SavedSearch:
        fields: dict[str, Any] = {"owner_id": owner.id, "name": "Finans web", "query": "vlan:41"}
        fields.update(values)
        return SavedSearch(**fields)

    db_session.add(search(range_preset="24h"))
    db_session.add(search(name="Sabit", range_from=start, range_to=start + timedelta(days=1)))
    db_session.add(search(owner_id=other.id, range_preset="7d"))  # the same name, another owner
    db_session.flush()
    _refused(
        db_session, search(name="FİNANS WEB".replace("İ", "I"), range_preset="1h"), "owner_name"
    )
    _refused(db_session, search(name="", range_preset="1h"), "ck_saved_searches_name")
    _refused(db_session, search(name="x" * 81, range_preset="1h"), "ck_saved_searches_name")
    _refused(db_session, search(name="q", query="x" * 1001, range_preset="1h"), "query")
    _refused(db_session, search(name="p", range_preset="2h"), "ck_saved_searches_range_preset")
    _refused(db_session, search(name="n"), "ck_saved_searches_range")
    _refused(
        db_session,
        search(name="b", range_preset="1h", range_from=start, range_to=start + timedelta(1)),
        "ck_saved_searches_range",
    )
    _refused(
        db_session, search(name="r", range_from=start, range_to=start), "ck_saved_searches_range"
    )
    db_session.commit()
    db_session.delete(owner)
    db_session.commit()
    names = db_session.scalars(select(SavedSearch.name)).all()
    assert names == ["Finans web"]  # the owner's searches went with the owner
