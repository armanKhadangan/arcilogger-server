"""The worker's audit writer (``arcilogger_db.audit.append_audit``) and the API's
``record_audit`` write one chain: both are the single chain step
``arcilogger_db.audit.append_entry`` (same canonical entry, hash, lock and isolation check), and
the API's spool flush copies the worker's entries with its own.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import audit as api_audit
from arcilogger_api.audit import (
    SystemActor,
    flush_audit_spool,
    record_audit,
    verify_audit_chain,
    verify_audit_lines,
)
from arcilogger_core.layout import Layout
from arcilogger_db import audit as db_audit
from arcilogger_db.audit import append_audit
from arcilogger_db.engine import session_factory
from arcilogger_db.models import AuditLog


def test_there_is_one_chain_step() -> None:
    """The API re-exports the shared implementation instead of keeping its own copy."""
    for name in (
        "GENESIS_HASH",
        "AUDIT_LOCK_KEY",
        "SPOOL_LOCK_KEY",
        "WATERMARK_KEY",
        "canonical_entry",
        "entry_hash",
        "entry_of",
        "spool_line",
        "repair_torn_tail",
        "verify_audit_chain",
        "verify_audit_lines",
    ):
        assert getattr(api_audit, name) is getattr(db_audit, name), name
    assert api_audit._append is db_audit.append_bytes


def test_worker_jobs_and_api_requests_interleave_into_one_chain(
    app_session_factory: sessionmaker[Session], layout: Layout, db_engine: Engine
) -> None:
    """Writers in several threads, half through the worker's ``append_audit`` (on the worker's
    own sessions, as its jobs write), half through the API's ``record_audit``: one gap-free
    chain, in the database and in the spool the API writes (the worker's entries included)."""
    worker = session_factory(db_engine)
    rounds = 12

    def as_worker(n: int) -> None:
        with worker() as session:
            append_audit(session, action="evidence.purge", target="exports", details={"n": n})
            session.commit()

    def as_api(n: int) -> None:
        with app_session_factory() as session:
            record_audit(session, actor=SystemActor(), action="test.api", details={"n": n})
            session.commit()

    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in [pool.submit(as_worker if n % 2 else as_api, n) for n in range(2 * rounds)]:
            future.result()

    with app_session_factory() as session:
        result = verify_audit_chain(session)
        assert result.ok, result.errors
        assert result.checked == 2 * rounds
        actions = [row.action for row in session.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions.count("evidence.purge") == rounds
    assert actions.count("test.api") == rounds
    flush_audit_spool(db_engine, layout)
    lines = b"".join(
        path.read_bytes() for path in sorted((layout.spool / "_audit").iterdir())
    ).splitlines()
    assert len(lines) == 2 * rounds
    spooled = verify_audit_lines(lines, prev_hash=db_audit.GENESIS_HASH)
    assert spooled.ok, spooled.errors
    assert spooled.last_hash == result.last_hash


def test_constants_are_the_same() -> None:
    assert db_audit.AUDIT_LOCK_KEY == api_audit.AUDIT_LOCK_KEY
    assert db_audit.GENESIS_HASH == api_audit.GENESIS_HASH
    assert db_audit.ENTRY_VERSION == api_audit.ENTRY_VERSION
    assert SystemActor().username == db_audit.SYSTEM_ACTOR


def test_both_writers_build_one_verifiable_chain(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    db_engine: Engine,
) -> None:
    with app_session_factory() as session:
        record_audit(session, actor=SystemActor(), action="test.api", details={"n": 1})
        session.commit()
    with app_session_factory() as session:
        row = append_audit(
            session,
            action="seal.run",
            target="seal:2026-09-10",
            details={"day": date(2026, 9, 10), "files": [1, 2], "ok": True, "none": None},
            ip="172.20.10.37",
        )
        session.commit()
        assert row.details == {"day": "2026-09-10", "files": [1, 2], "ok": True, "none": None}
        assert row.actor_username == "system"
        assert row.actor_id is None
    with app_session_factory() as session:
        record_audit(session, actor=SystemActor(), action="test.api", details={"n": 2})
        session.commit()
    with app_session_factory() as session:
        result = verify_audit_chain(session)
        assert result.ok, result.errors
        assert result.checked == 3
        rows = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        # The entry hashes exactly as the API computes it.
        middle = rows[1]
        assert middle.hash == api_audit.entry_hash(middle.prev_hash, api_audit.entry_of(middle))
        assert middle.ip == "172.20.10.37"
    # The API's flush puts the worker's entry into the spool like its own.
    flush_audit_spool(db_engine, layout)
    spooled = b"".join(path.read_bytes() for path in sorted((layout.spool / "_audit").iterdir()))
    lines = spooled.splitlines()
    assert len(lines) == 3
    assert verify_audit_lines(lines).ok


@pytest.mark.parametrize(
    ("details", "message"),
    [
        ({"ratio": 0.5}, "exactly"),
        ({"text": "a\x00b"}, "NUL"),
    ],
)
def test_values_the_chain_cannot_hold_are_refused(
    app_session_factory: sessionmaker[Session], details: dict[str, object], message: str
) -> None:
    with app_session_factory() as session, pytest.raises(ValueError, match=message):
        append_audit(session, action="x.y", details=details)


def test_action_and_isolation_are_checked(app_session_factory: sessionmaker[Session]) -> None:
    with app_session_factory() as session, pytest.raises(ValueError, match="action"):
        append_audit(session, action="")
    with app_session_factory() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        with pytest.raises(RuntimeError, match="READ COMMITTED"):
            append_audit(session, action="x.y")
