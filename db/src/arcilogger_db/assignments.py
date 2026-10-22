"""Device → person assignments with history (spec 9.2, ADR-0008).

Who held a device when: ``device_assignments`` rows ``[valid_from, valid_to)``, ``valid_to``
NULL for the current one; ``devices.person_id`` mirrors the open row. A person may hold several
devices (a PC, a phone, the phone's next randomised MAC); a device has at most one person at a
time. Every change goes through :class:`Planner` and :func:`apply`, in the caller's
transaction (``POST /devices/assign`` and the CSV import of the API):

1. :func:`lock_devices` locks the device rows (``SELECT … FOR UPDATE`` in MAC order — the order
   in which the worker's device upsert writes them, so the two never deadlock) and
   :func:`load_history` reads their assignments;
2. :meth:`Planner.assign` checks one change against the device's history as it stands, including
   the changes planned before it (an import may move one device through several persons):
   ``valid_from`` not in the future (a start within :data:`FUTURE_SLACK` of clock skew is taken
   as now) and not before the current assignment's start or, for an unassigned device, the end
   of its last one — history is only ever appended to, never rewritten. Assigning the current
   owner again, or unassigning a device without one, changes nothing. A note is kept on the
   assignment it opens; an unassign opens none, so it takes no note;
3. :func:`apply` closes the open rows at ``valid_from``, inserts the new ones and updates
   ``devices.person_id``.

The database refuses an overlap on its own too (exclusion constraint or trigger, revision
``0008_device_assignments``); :func:`is_overlap_error` recognises that error, e.g. after a
concurrent change the lock did not cover.

Reading (the lookup engine's person step): :func:`load_history` and :func:`covering`.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_db.models import Device, DeviceAssignment

#: A ``valid_from`` up to this far ahead of the server clock counts as "now" and is stored as
#: now (browser clocks); further ahead it is refused.
FUTURE_SLACK: Final = timedelta(minutes=2)
#: Name of the database guard (exclusion constraint, or the trigger's error).
OVERLAP_CONSTRAINT: Final = "ex_device_assignments_no_overlap"
#: PostgreSQL ``exclusion_violation``.
EXCLUSION_VIOLATION: Final = "23P01"
#: Device rows locked per statement (keeps the ``IN`` list of one statement bounded).
_CHUNK: Final = 1000


@dataclass(eq=False)
class Slot:
    """One assignment interval as the planner sees it (a stored row or a planned one)."""

    person_id: int
    valid_from: datetime
    valid_to: datetime | None = None
    backfilled: bool = False
    #: ``device_assignments.id``; ``None`` for a row planned in this batch.
    id: int | None = None
    note: str | None = None

    def covers(self, at: datetime) -> bool:
        return self.valid_from <= at and (self.valid_to is None or at < self.valid_to)


@dataclass(frozen=True)
class Change:
    """One device's assignment change: the open slot closed (if any) and the one opened."""

    mac: str
    #: The device's owner before (``None``: it had none).
    previous: int | None
    #: New owner; ``None`` = unassigned.
    person_id: int | None
    valid_from: datetime
    closed: Slot | None
    opened: Slot | None


class AssignmentError(ValueError):
    """A change the history does not allow (``code``: ``in_future`` or ``overlap``)."""

    def __init__(self, code: str, mac: str, *, limit: datetime | None = None) -> None:
        self.code = code
        self.mac = mac
        #: ``overlap``: the earliest allowed ``valid_from``.
        self.limit = limit
        super().__init__(f"{code}: {mac}")


@dataclass
class Planner:
    """Plans changes against the history of the locked devices (in memory, nothing written)."""

    history: dict[str, list[Slot]]
    now: datetime
    #: ``devices.person_id`` of the locked devices (:func:`lock_devices`). An owner without an
    #: open slot was written outside this module (before the history existed): it can be
    #: replaced or removed like any other, and assigning it again starts its history.
    owners: dict[str, int | None] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)

    def current(self, mac: str) -> Slot | None:
        return next((slot for slot in self.history.get(mac, ()) if slot.valid_to is None), None)

    def earliest(self, mac: str) -> datetime | None:
        """The earliest ``valid_from`` a new change of ``mac`` may have."""
        slots = self.history.get(mac, ())
        return max(
            (slot.valid_from if slot.valid_to is None else slot.valid_to for slot in slots),
            default=None,
        )

    def assign(
        self,
        mac: str,
        person_id: int | None,
        valid_from: datetime | None = None,
        *,
        note: str | None = None,
    ) -> Change | None:
        """Plan ``mac`` → ``person_id`` (``None`` = unassign) from ``valid_from`` (default: now).

        Returns ``None`` when nothing changes; raises :class:`AssignmentError` otherwise. A
        ``note`` belongs to the assignment opened, so an unassign refuses one (``ValueError``)
        rather than dropping it.
        """
        if person_id is None and note is not None:
            raise ValueError("an unassign opens no assignment and takes no note")
        start = self.now if valid_from is None else valid_from
        if start.utcoffset() is None:
            raise ValueError("valid_from must be timezone-aware")
        if start > self.now + FUTURE_SLACK:
            raise AssignmentError("in_future", mac)
        # Within the slack the start *is* now: a browser clock a little ahead must not leave a
        # future moment as the stored start (and as the earliest start of the next change).
        start = min(start, self.now)
        current = self.current(mac)
        owner = current.person_id if current is not None else self.owners.get(mac)
        if owner is None and person_id is None:
            return None
        if current is not None and current.person_id == person_id:
            return None
        limit = self.earliest(mac)
        if limit is not None and start < limit:
            raise AssignmentError("overlap", mac, limit=limit)
        if current is not None:
            current.valid_to = start
        opened = None
        if person_id is not None:
            opened = Slot(person_id=person_id, valid_from=start, note=note)
            self.history.setdefault(mac, []).append(opened)
        self.owners[mac] = person_id
        change = Change(
            mac=mac,
            previous=owner,
            person_id=person_id,
            valid_from=start,
            closed=current,
            opened=opened,
        )
        self.changes.append(change)
        return change


def lock_devices(session: Session, macs: Collection[str]) -> dict[str, int | None]:
    """Lock the ``devices`` rows of ``macs`` (MAC order); MAC → current owner of those that
    exist (pass it to :class:`Planner` as ``owners``)."""
    found: dict[str, int | None] = {}
    ordered = sorted(set(macs))
    for start in range(0, len(ordered), _CHUNK):
        chunk = ordered[start : start + _CHUNK]
        rows = session.execute(
            select(Device.mac, Device.person_id)
            .where(Device.mac.in_(chunk))
            .order_by(Device.mac)
            .with_for_update()
        )
        found.update((mac, person_id) for mac, person_id in rows)
    return found


def load_history(session: Session, macs: Iterable[str]) -> dict[str, list[Slot]]:
    """Every assignment of ``macs``, oldest first (MACs without one are missing)."""
    wanted = sorted(set(macs))
    history: dict[str, list[Slot]] = {}
    for start in range(0, len(wanted), _CHUNK):
        rows = session.scalars(
            select(DeviceAssignment)
            .where(DeviceAssignment.mac.in_(wanted[start : start + _CHUNK]))
            .order_by(DeviceAssignment.mac, DeviceAssignment.valid_from, DeviceAssignment.id)
        )
        for row in rows:
            history.setdefault(row.mac, []).append(
                Slot(
                    person_id=row.person_id,
                    valid_from=row.valid_from,
                    valid_to=row.valid_to,
                    backfilled=row.backfilled,
                    id=row.id,
                    note=row.note,
                )
            )
    return history


def covering(slots: Sequence[Slot], at: datetime) -> Slot | None:
    """The slot valid at ``at`` (``valid_from <= at < valid_to``), else ``None``."""
    return next((slot for slot in reversed(slots) if slot.covers(at)), None)


def apply(
    session: Session, changes: Sequence[Change], *, assigned_by: str
) -> list[DeviceAssignment]:
    """Write planned changes (in plan order); returns the new rows (flushed, with ids).

    Stored rows are closed first, then the new rows are inserted — a row planned and closed in
    the same batch is inserted closed — and ``devices.person_id`` is set to each device's final
    owner.
    """
    for change in changes:
        closed = change.closed
        if closed is not None and closed.id is not None:
            session.execute(
                update(DeviceAssignment)
                .where(DeviceAssignment.id == closed.id, DeviceAssignment.valid_to.is_(None))
                .values(valid_to=closed.valid_to)
            )
    planned = [(change.mac, change.opened) for change in changes if change.opened is not None]
    rows = [
        DeviceAssignment(
            mac=mac,
            person_id=slot.person_id,
            valid_from=slot.valid_from,
            # Set when a later change of this batch already closed it again.
            valid_to=slot.valid_to,
            assigned_by=assigned_by,
            note=slot.note,
            backfilled=False,
        )
        for mac, slot in planned
    ]
    session.add_all(rows)
    session.flush()
    for row, (_, slot) in zip(rows, planned, strict=True):
        slot.id = row.id
    owners: dict[str, int | None] = {}
    for change in changes:
        owners[change.mac] = change.person_id
    for mac, person_id in sorted(owners.items()):
        session.execute(
            update(Device)
            .where(Device.mac == mac)
            .values(person_id=person_id, updated_at=func.now())
        )
    return rows


def is_overlap_error(exc: DBAPIError) -> bool:
    """The database refused an overlapping assignment (constraint or trigger)."""
    original = exc.orig
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if code == EXCLUSION_VIOLATION:
        return True
    return OVERLAP_CONSTRAINT in str(original) or "uq_device_assignments_open_mac" in str(original)
