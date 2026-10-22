"""Device-person assignment history (revision ``0008_device_assignments``, ADR-0008): the planner's
rules, the database guard against overlaps (exclusion constraint, and the trigger used where
``btree_gist`` cannot be created), writing changes, and the migration's backfill and downgrade."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_db.assignments import (
    FUTURE_SLACK,
    AssignmentError,
    Planner,
    Slot,
    apply,
    covering,
    is_overlap_error,
    load_history,
    lock_devices,
)
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, upgrade
from arcilogger_db.models import Device, DeviceAssignment, Person
from arcilogger_db.testing import TestServer

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
MAC, OTHER = "f0:1f:af:00:00:01", "02:00:5e:00:53:46"


def at(days: float) -> datetime:
    return NOW - timedelta(days=days)


# -- planner (no database) ------------------------------------------------------------------------


def test_planner_appends_and_never_rewrites() -> None:
    planner = Planner({}, now=NOW)
    first = planner.assign(MAC, 1, at(10), note="zimmet")
    assert first is not None
    assert (first.previous, first.person_id, first.closed) == (None, 1, None)
    assert planner.assign(MAC, 1, at(5)) is None  # same owner: unchanged
    second = planner.assign(MAC, 2, at(5))
    assert second is not None
    assert second.previous == 1
    assert second.closed is first.opened
    assert first.opened is not None
    assert first.opened.valid_to == at(5)
    with pytest.raises(AssignmentError) as refused:
        planner.assign(MAC, 3, at(6))
    assert (refused.value.code, refused.value.limit) == ("overlap", at(5))
    gone = planner.assign(MAC, None, at(1))
    assert gone is not None
    assert gone.opened is None
    assert planner.current(MAC) is None
    assert planner.assign(MAC, None) is None  # nothing to close
    with pytest.raises(AssignmentError, match="overlap"):
        planner.assign(MAC, 4, at(2))  # inside the closed assignment
    assert planner.assign(MAC, 4, at(1)) is not None  # right where the last one ended
    with pytest.raises(AssignmentError, match="in_future"):
        planner.assign(OTHER, 1, NOW + FUTURE_SLACK + timedelta(seconds=1))
    assert planner.assign(OTHER, 1, NOW + FUTURE_SLACK) is not None
    with pytest.raises(ValueError, match="timezone-aware"):
        planner.assign(OTHER, 2, datetime(2026, 10, 1))  # noqa: DTZ001 - refused on purpose
    assert [c.mac for c in planner.changes] == [MAC, MAC, MAC, MAC, OTHER]


def test_a_start_within_the_slack_is_stored_as_now() -> None:
    """A browser clock up to :data:`FUTURE_SLACK` ahead: the start is now, not that future moment
    (which would become the earliest start of the next change and refuse an immediate fix)."""
    planner = Planner({}, now=NOW)
    ahead = planner.assign(MAC, 1, NOW + timedelta(seconds=90), note="zimmet")
    assert ahead is not None
    assert ahead.opened is not None
    assert (ahead.valid_from, ahead.opened.valid_from) == (NOW, NOW)
    assert planner.earliest(MAC) == NOW
    fixed = planner.assign(MAC, 2)  # the wrong person was chosen: corrected right away
    assert fixed is not None
    assert fixed.closed is ahead.opened
    assert (ahead.opened.valid_from, ahead.opened.valid_to) == (NOW, NOW)
    assert covering(planner.history[MAC], NOW) is fixed.opened
    # A start in the past is kept as it is; beyond the slack it is still refused.
    past = planner.assign(OTHER, 1, at(1))
    assert past is not None
    assert past.valid_from == at(1)
    with pytest.raises(AssignmentError, match="in_future"):
        planner.assign(OTHER, 2, NOW + FUTURE_SLACK + timedelta(microseconds=1))


def test_an_unassign_takes_no_note() -> None:
    """The note belongs to the assignment opened; an unassign opens none and refuses one rather
    than dropping it."""
    planner = Planner({}, now=NOW)
    planner.assign(MAC, 1, at(2), note="zimmet")
    with pytest.raises(ValueError, match="no note"):
        planner.assign(MAC, None, at(1), note="iade formu 42")
    assert planner.current(MAC) is not None  # nothing planned
    assert len(planner.changes) == 1
    gone = planner.assign(MAC, None, at(1))
    assert gone is not None
    assert gone.closed is not None
    assert gone.closed.note == "zimmet"


def test_the_same_moment_closes_with_an_empty_interval() -> None:
    planner = Planner({}, now=NOW)
    planner.assign(MAC, 1, at(1))
    change = planner.assign(MAC, 2, at(1))
    assert change is not None
    assert change.closed is not None
    assert change.closed.valid_from == change.closed.valid_to  # covers no moment
    assert covering(planner.history[MAC], at(1)) is change.opened


def test_an_owner_written_without_history_can_be_replaced_or_removed() -> None:
    """``devices.person_id`` set outside this module (before the history existed): unassigning
    clears it, assigning the same person again starts the history."""
    planner = Planner({}, now=NOW, owners={MAC: 7, OTHER: 7})
    removed = planner.assign(MAC, None, at(1))
    assert removed is not None
    assert (removed.previous, removed.person_id, removed.closed, removed.opened) == (
        7,
        None,
        None,
        None,
    )
    assert planner.assign(MAC, None) is None  # now really without owner
    again = planner.assign(OTHER, 7, at(2))
    assert again is not None
    assert again.previous == 7
    assert again.opened is not None


def test_covering() -> None:
    slots = [Slot(1, at(10), at(5)), Slot(2, at(5), None)]
    assert covering(slots, at(10)) is slots[0]
    assert covering(slots, at(5)) is slots[1]  # the end is exclusive
    assert covering(slots, at(11)) is None
    assert covering(slots, NOW) is slots[1]


# -- database -------------------------------------------------------------------------------------


@pytest.fixture
def devices(db_session: Session) -> tuple[int, int]:
    first, second = Person(name="Ayşe"), Person(name="Mehmet")
    db_session.add_all([first, second, Device(mac=MAC), Device(mac=OTHER)])
    db_session.commit()
    return first.id, second.id


def _insert(session: Session, person: int, start: datetime, end: datetime | None) -> None:
    session.add(
        DeviceAssignment(mac=MAC, person_id=person, valid_from=start, valid_to=end, assigned_by="t")
    )
    session.flush()


def _assert_guarded(session: Session, ayse: int, mehmet: int) -> None:
    _insert(session, ayse, at(10), at(5))
    _insert(session, mehmet, at(5), at(5))  # empty interval: allowed
    _insert(session, mehmet, at(5), None)
    session.commit()
    for start, end in ((at(7), at(6)), (at(3), at(2)), (at(12), at(9))):
        with pytest.raises(IntegrityError) as overlap:
            _insert(session, ayse, start, end)
        assert is_overlap_error(overlap.value), (start, end)
        session.rollback()
    with pytest.raises(IntegrityError) as second_open:
        _insert(session, ayse, NOW, None)
    assert is_overlap_error(second_open.value)
    session.rollback()
    with pytest.raises(IntegrityError, match="valid_interval"):
        _insert(session, ayse, at(1), at(2))
    session.rollback()
    # Shrinking the open interval is an update the guard checks too.
    row = session.scalars(select(DeviceAssignment).where(DeviceAssignment.valid_to.is_(None))).one()
    row.valid_from = at(8)
    with pytest.raises(IntegrityError) as moved:
        session.flush()
    assert is_overlap_error(moved.value)
    session.rollback()


def test_the_database_refuses_overlaps(db_session: Session, devices: tuple[int, int]) -> None:
    constraint = db_session.scalar(
        text("SELECT contype FROM pg_constraint WHERE conname = 'ex_device_assignments_no_overlap'")
    )
    assert constraint == "x"  # btree_gist (contrib) is available on the test servers
    _assert_guarded(db_session, *devices)


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


def _upgrade_to_head(engine: Engine, *, guard: str = "auto") -> None:
    with engine.begin() as connection:
        config = alembic_config(connection)
        config.attributes["assignment_guard"] = guard
        command.upgrade(config, "head")


def test_the_trigger_guards_without_btree_gist(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision="0007_evidence_seals")
    _upgrade_to_head(empty_engine, guard="trigger")
    with Session(empty_engine) as session:
        assert session.scalar(text("SELECT count(*) FROM pg_constraint WHERE contype = 'x'")) == 0
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = 'device_assignments_no_overlap'"
                )
            )
            == 1
        )
        first, second = Person(name="Ayşe"), Person(name="Mehmet")
        session.add_all([first, second, Device(mac=MAC)])
        session.commit()
        _assert_guarded(session, first.id, second.id)
        # The trigger reports the same SQLSTATE as the constraint.
        with pytest.raises(DBAPIError) as raised:
            _insert(session, first.id, at(7), at(6))
        original = raised.value.orig
        assert getattr(original, "sqlstate", None) == "23P01"
        session.rollback()


def test_apply_writes_history_and_the_current_owner(
    db_session: Session, devices: tuple[int, int]
) -> None:
    ayse, mehmet = devices
    owners = lock_devices(db_session, [MAC, OTHER, "00:00:00:00:00:01"])
    assert owners == {MAC: None, OTHER: None}
    planner = Planner(load_history(db_session, [MAC, OTHER]), now=NOW, owners=owners)
    planner.assign(MAC, ayse, at(10), note="ilk")
    planner.assign(MAC, mehmet, at(4))  # closes the row planned just before
    planner.assign(OTHER, ayse, at(2))
    rows = apply(db_session, planner.changes, assigned_by="yonetici")
    db_session.commit()
    assert [r.id is not None for r in rows] == [True, True, True]
    assert db_session.get_one(Device, MAC).person_id == mehmet
    assert db_session.get_one(Device, OTHER).person_id == ayse
    history = load_history(db_session, [MAC])[MAC]
    assert [(s.person_id, s.valid_from, s.valid_to, s.note) for s in history] == [
        (ayse, at(10), at(4), "ilk"),
        (mehmet, at(4), None, None),
    ]
    # A second batch continues from the stored state.
    later = Planner(load_history(db_session, [MAC]), now=NOW)
    later.assign(MAC, None, at(1))
    apply(db_session, later.changes, assigned_by="yonetici")
    db_session.commit()
    assert db_session.get_one(Device, MAC).person_id is None
    assert [s.valid_to for s in load_history(db_session, [MAC])[MAC]] == [at(4), at(1)]


def test_migration_backfills_existing_links_and_downgrades(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision="0007_evidence_seals")
    seen = datetime(2026, 3, 12, 6, 0, tzinfo=UTC)
    with empty_engine.begin() as connection:
        person = connection.execute(
            text("INSERT INTO persons (name) VALUES ('Ayşe') RETURNING id")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO devices (mac, person_id, first_seen) VALUES "
                "(:a, :p, :seen), (:b, :p, NULL), (:c, NULL, :seen)"
            ),
            {"a": MAC, "b": OTHER, "c": "00:0c:29:00:00:99", "p": person, "seen": seen},
        )
        created = connection.execute(
            text("SELECT created_at FROM devices WHERE mac = :b"), {"b": OTHER}
        ).scalar_one()
    _upgrade_to_head(empty_engine)
    with empty_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT mac::text, person_id, valid_from, valid_to, assigned_by, backfilled "
                "FROM device_assignments ORDER BY mac"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        (OTHER, person, created, None, ":migration", True),
        (MAC, person, seen, None, ":migration", True),
    ]
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0007_evidence_seals")
    assert "device_assignments" not in inspect(empty_engine).get_table_names()
    with empty_engine.connect() as connection:
        owners = connection.execute(
            text("SELECT count(*) FROM devices WHERE person_id IS NOT NULL")
        ).scalar_one()
        functions = connection.execute(
            text(
                "SELECT count(*) FROM pg_proc WHERE proname = 'arci_device_assignments_no_overlap'"
            )
        ).scalar_one()
    assert (owners, functions) == (2, 0)  # the current links stay
    _upgrade_to_head(empty_engine)  # and up again: backfilled once more
    with empty_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM device_assignments")).scalar_one() == 2
