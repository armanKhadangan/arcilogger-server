"""Source clocks by receive time: ``source_clock_samples`` (ADR-0016 D28, issue #73).

Every ``messages`` row with a device time (``time <> time_received``) counts once under the key
:class:`ClockKey`: its source, the minute it was **received** (UTC) and its device clock offset
``time - time_received`` in whole seconds rounded away from zero, as a lookup prints an offset (so
a row beyond a limit of whole seconds is stored beyond it), ``0`` under a minute either way
(:data:`NEAR_SECONDS`; no clock limit is that small, so a source on time has one key a minute).

* :func:`offset_of` and :func:`minute_of` define a key; :func:`clock_key` applies them to a row
  and :func:`rebuild` (which counts a source's received range again from its rows) uses it. The
  ingest loader adds a batch's samples in the transaction that loads its rows, as it adds
  ``search_counts``; it groups its lines by the same two functions (a test pins it to
  :func:`clock_key` line by line).
* :func:`upsert_samples` adds a :class:`collections.Counter` of keys (``INSERT … ON CONFLICT …
  DO UPDATE SET n = n + EXCLUDED.n``, keys in sorted order so two writers never deadlock).
* :func:`received_between` and :func:`newest` read one source's offsets of a received range as
  ``(offset, rows)`` pairs; :func:`median` is their median, as ``percentile_cont(0.5)`` gives it.

The readers are the ``R20-lease:source-clock`` alert (the newest received minutes) and a lookup's
clock check (the minutes received around its moment). A clock set hours back writes rows whose
device times lie hours before their arrival: a device-time sample of ``messages`` reaches them
only after the offset itself, the minutes they were received at once.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, NamedTuple

from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.orm import Session

from arcilogger_db.models import Message, SourceClockSample

#: An offset smaller than this either way is stored as ``0`` (seconds).
NEAR_SECONDS: Final = 60
#: A row's device time lies within this of its receive time when it is used (ingest's
#: ``DEFAULT_MAX_CLOCK_SKEW``; a time further off is replaced by the receive time and not counted).
MAX_CLOCK_SKEW: Final = timedelta(days=1)
#: Keys per statement of :func:`upsert_samples`.
UPSERT_CHUNK: Final = 500
#: :func:`upsert_samples`' one statement: the keys as parallel arrays, the same text for every
#: batch (as ``searchcounts.UPSERT``).
UPSERT: Final = text(
    "INSERT INTO source_clock_samples (source, minute, seconds, n) "
    "SELECT * FROM unnest(CAST(:sources AS text[]), CAST(:minutes AS timestamptz[]), "
    "CAST(:offsets AS integer[]), CAST(:ns AS bigint[])) "
    "ON CONFLICT ON CONSTRAINT pk_source_clock_samples "
    "DO UPDATE SET n = source_clock_samples.n + EXCLUDED.n"
)


class ClockKey(NamedTuple):
    """One row of ``source_clock_samples`` without its count (``seconds``: :func:`offset_of`)."""

    source: str
    minute: datetime
    seconds: int


def offset_of(time: datetime, received: datetime) -> int:
    """``time - received`` in whole seconds, rounded up away from zero (120.4 s is 121, -120.4 s
    is -121: an offset beyond a limit of whole seconds stays beyond it), ``0`` under
    :data:`NEAR_SECONDS` either way."""
    seconds = (time - received).total_seconds()
    if abs(seconds) < NEAR_SECONDS:
        return 0
    whole = math.ceil(abs(seconds))
    return -whole if seconds < 0 else whole


def minute_of(received: datetime) -> datetime:
    """The UTC minute ``received`` falls in."""
    return received.astimezone(UTC).replace(second=0, microsecond=0)


def clock_key(message: Mapping[str, Any]) -> ClockKey | None:
    """The key of a ``messages`` row, ``None`` when its time is the receive time (no device
    time was usable)."""
    time, received = message["time"], message["time_received"]
    if time == received:
        return None
    return ClockKey(message["source"], minute_of(received), offset_of(time, received))


def upsert_samples(connection: Connection, samples: Mapping[ClockKey, int]) -> int:
    """Add ``samples`` to ``source_clock_samples`` (inside the caller's transaction); keys
    written."""
    keys = sorted(key for key, n in samples.items() if n > 0)
    for start in range(0, len(keys), UPSERT_CHUNK):
        chunk = keys[start : start + UPSERT_CHUNK]
        connection.execute(
            UPSERT,
            {
                "sources": [key.source for key in chunk],
                "minutes": [key.minute for key in chunk],
                "offsets": [key.seconds for key in chunk],
                "ns": [samples[key] for key in chunk],
            },
        )
    return len(keys)


def rebuild(connection: Connection, source: str, start: datetime, end: datetime) -> int:
    """Count ``source``'s samples of the minutes received in ``[start, end)`` again from its
    ``messages`` rows (the caller's transaction; whole minutes); keys written."""
    first, last = minute_of(start), minute_of(end)
    connection.execute(
        delete(SourceClockSample).where(
            SourceClockSample.source == source,
            SourceClockSample.minute >= first,
            SourceClockSample.minute < last,
        )
    )
    m = Message.__table__
    rows = connection.execute(
        select(m.c.time, m.c.time_received).where(
            m.c.source == source,
            m.c.time_received >= first,
            m.c.time_received < last,
            m.c.time >= first - MAX_CLOCK_SKEW,
            m.c.time <= last + MAX_CLOCK_SKEW,
            m.c.time != m.c.time_received,
        )
    )
    samples: dict[ClockKey, int] = {}
    for time, received in rows:
        key = clock_key({"source": source, "time": time, "time_received": received})
        if key is not None:
            samples[key] = samples.get(key, 0) + 1
    return upsert_samples(connection, samples)


# -- reading --------------------------------------------------------------------------------------


def received_between(
    session: Session | Connection, source: str, start: datetime, end: datetime
) -> list[tuple[int, int]]:
    """``source``'s ``(offset, rows)`` of the minutes received in ``[start, end]``."""
    t = SourceClockSample
    query = (
        select(t.seconds, func.sum(t.n))
        .where(t.source == source, t.minute >= minute_of(start), t.minute <= end)
        .group_by(t.seconds)
    )
    return [(int(offset), int(n)) for offset, n in session.execute(query)]


def newest(
    session: Session | Connection,
    source: str,
    since: datetime,
    until: datetime,
    rows: int,
    *,
    least: int = 0,
    recent: datetime | None = None,
) -> list[tuple[int, int]]:
    """``source``'s ``(offset, rows)`` of its newest minutes received in ``[since, until]``,
    taken whole, newest first, until they hold ``rows`` rows (all of them when they hold
    fewer), or until they hold ``least`` rows and the next minute was received before
    ``recent`` (a quiet source is judged on its recent minutes, not on its whole range)."""
    t = SourceClockSample
    query = (
        select(t.minute, t.seconds, t.n)
        .where(t.source == source, t.minute >= minute_of(since), t.minute <= until)
        .order_by(t.minute.desc())
    )
    taken: dict[int, int] = {}
    total, last = 0, None
    for minute, offset, n in session.execute(query):
        if minute != last and (
            total >= rows or (recent is not None and total >= least and minute < recent)
        ):
            break
        taken[int(offset)] = taken.get(int(offset), 0) + int(n)
        total += int(n)
        last = minute
    return list(taken.items())


def median(histogram: Sequence[tuple[int, int]]) -> tuple[float | None, int]:
    """The median of ``(offset, rows)`` pairs as ``percentile_cont(0.5)`` gives it over the
    rows, and the number of rows (``None`` without rows)."""
    pairs = sorted((offset, n) for offset, n in histogram if n > 0)
    count = sum(n for _offset, n in pairs)
    if not count:
        return None, 0
    position = (count - 1) / 2
    low, high = math.floor(position), math.ceil(position)
    values: dict[int, int] = {}
    seen = 0
    for offset, n in pairs:
        for index in (low, high):
            if index not in values and seen <= index < seen + n:
                values[index] = offset
        seen += n
    return values[low] + (values[high] - values[low]) * (position - low), count
