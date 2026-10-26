"""Load one batch in **one transaction**: rows, failures, sources, alerts, the search counts and
the offsets.

Exactly-once rests on two things done here:

1. The new ``ingest_offsets`` values are written in the same transaction as the ``COPY`` of the
   rows they cover. A crash, a kill or a lost connection at any point before ``COMMIT`` leaves
   neither; after it, both.
2. The offset update is a **compare-and-set**: ``UPDATE … WHERE byte_offset = <the offset this
   batch was read from> AND line_number = … AND finished_at IS NULL``. It runs first (and takes
   the row locks), so if anything else moved the offset — a second ingest instance by mistake,
   an operator — the batch is refused (:class:`OffsetConflictError`) instead of loading lines twice.

The search summary table ``search_counts`` (:mod:`arcilogger_db.searchcounts`, ADR-0015) is
written in the same transaction (step ``counts``), from exactly the lines whose rows loaded, so the
counts are exactly-once like the rows: a line whose typed row ``COPY`` refused counts with
``tbl = ''`` (the search over typed tables does not find it either), a line without a ``messages``
row (``rawlog.invalid``, or a ``messages`` row ``COPY`` refused) does not count. One ingest writes
a file, and a finished file is never written again, so the counts need no lock of their own.

A row that passed :func:`~arcilogger_db.rowcheck.check_row` should always load. If ``COPY``
still rejects something (``DataError``/``IntegrityError``, or a value the driver cannot encode),
the batch is **bisected** inside savepoints until the line responsible is found; that line is
counted as ``copy.<table>`` and its ``messages`` row is loaded alone if possible. Everything
else in the batch loads normally: one bad line never blocks ingest. Errors that are not about
the data (connection lost, server shutting down, missing schema) abort the whole transaction
and the caller retries the same batch later.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import Connection, Engine, func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

from arcilogger_core.timeutil import isoformat_site, utc_now
from arcilogger_db.bulk import copy_rows
from arcilogger_db.kinds import CLIENT_IP
from arcilogger_db.models import Alert, IngestOffset, ParseFailure, Source
from arcilogger_db.rowcheck import check_row
from arcilogger_db.searchcounts import CountKey, count_key, net_of, upsert_counts
from arcilogger_ingest.prepare import DETAIL_MAX, Failure, PreparedLine

log = logging.getLogger("arcilogger.ingest")

PATTERN_COPY: Final = "copy."
NEW_SOURCE_RULE: Final = "new_source"
#: Errors that mean "this data cannot be stored" (as opposed to "the database is unavailable").
DATA_ERRORS: Final = (DataError, IntegrityError, UnicodeError, ValueError, TypeError)

#: Called with the step name before each step (tests interrupt the transaction there).
StepHook = Callable[[str], None]
STEPS: Final = ("offsets", "copy", "failures", "sources", "counts", "commit")


class OffsetConflictError(RuntimeError):
    """An offset in the database is not where this batch started; nothing was loaded."""


class _CopyFailedError(Exception):
    def __init__(self, table: str, error: BaseException) -> None:
        super().__init__(f"{table}: {error}")
        self.table = table
        self.error = error


#: A line whose rows loaded, and whether its typed row loaded too (``False``: its ``messages`` row
#: was salvaged alone).
_Loaded = tuple[PreparedLine, bool]
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_HOUR: Final = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class OffsetUpdate:
    """Move ``(source, day)`` from ``old`` to ``new`` (offset, line) — or only finish it."""

    source: str
    day: date
    old_offset: int
    old_line: int
    new_offset: int
    new_line: int
    #: The row is known to exist (otherwise it is created at 0/0 first).
    exists: bool
    #: Set ``finished_at``: the day is sealed and ``new_offset`` is its sealed size.
    finish: bool = False


@dataclass(slots=True)
class SourceSeen:
    """What a batch saw from one source (for the ``sources`` row)."""

    name: str
    type: str
    unknown: bool
    #: Configured exact source: type, ``expected`` and heartbeat follow the configuration.
    configured: bool
    expected: bool
    heartbeat_minutes: int
    address: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def see(self, received: datetime | None, sender: str | None) -> None:
        if received is not None:
            if self.first_seen is None or received < self.first_seen:
                self.first_seen = received
            if self.last_seen is None or received > self.last_seen:
                self.last_seen = received
        # check_row: an address PostgreSQL refuses (IPv6 zone id) is not stored.
        if (
            sender is not None
            and sender != self.address
            and not check_row("sources", {"address": sender})
        ):
            self.address = sender


@dataclass(slots=True)
class Batch:
    """Everything one transaction loads."""

    lines: list[PreparedLine] = field(default_factory=list)
    offsets: dict[tuple[str, date], OffsetUpdate] = field(default_factory=dict)
    sources: dict[str, SourceSeen] = field(default_factory=dict)
    #: Failures found while loading (``copy.<table>``), in addition to the lines' own.
    load_failures: list[tuple[str, date, Failure]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.lines)

    @property
    def empty(self) -> bool:
        return not self.lines and not self.offsets and not self.sources


@dataclass(frozen=True, slots=True)
class LoadResult:
    rows: dict[str, int]
    failures: int
    new_sources: tuple[str, ...]

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


@dataclass(slots=True)
class _FailureAggregate:
    count: int
    first_seen: datetime
    last_seen: datetime
    example_line: int
    example_offset: int
    error: str | None


class Loader:
    def __init__(self, engine: Engine, *, step_hook: StepHook | None = None) -> None:
        self.engine = engine
        self.step_hook = step_hook

    def _step(self, name: str) -> None:
        if self.step_hook is not None:
            self.step_hook(name)

    def load(self, batch: Batch) -> LoadResult:
        """Load ``batch`` atomically; raises on any failure (then nothing was loaded)."""
        batch.load_failures.clear()
        with self.engine.begin() as connection:
            self._step("offsets")
            self._update_offsets(connection, batch.offsets.values())
            self._step("copy")
            counts: dict[str, int] = {}
            loaded: list[_Loaded] = []
            refused = self._copy_bisect(connection, batch.lines, counts, batch, loaded)
            if refused and not batch.load_failures:
                # Not one bad line but something about the batch as a whole: every line loaded
                # on its own, after up to 2 x lines extra COPYs. Silent, that would only show
                # up as ingest falling behind.
                log.warning(
                    "COPY refused a batch of %d lines %d time(s) although every line loads on "
                    "its own; this is slow, please report it as a bug",
                    len(batch.lines),
                    refused,
                )
            self._step("failures")
            failures = self._record_failures(connection, batch)
            self._step("sources")
            new_sources = self._update_sources(connection, batch.sources.values())
            self._step("counts")
            upsert_counts(connection, _count_keys(loaded))
            self._step("commit")
        return LoadResult(rows=counts, failures=failures, new_sources=new_sources)

    # -- offsets ----------------------------------------------------------------------------------

    def _update_offsets(self, connection: Connection, updates: Iterable[OffsetUpdate]) -> None:
        for change in sorted(updates, key=lambda u: (u.source, u.day)):  # fixed lock order
            if not change.exists:
                connection.execute(
                    insert(IngestOffset)
                    .values(source=change.source, day=change.day, byte_offset=0, line_number=0)
                    .on_conflict_do_nothing(index_elements=["source", "day"])
                )
            values: dict[str, Any] = {
                "byte_offset": change.new_offset,
                "line_number": change.new_line,
            }
            if change.finish:
                values["finished_at"] = func.now()
            result = connection.execute(
                update(IngestOffset)
                .where(
                    IngestOffset.source == change.source,
                    IngestOffset.day == change.day,
                    IngestOffset.byte_offset == change.old_offset,
                    IngestOffset.line_number == change.old_line,
                    IngestOffset.finished_at.is_(None),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise OffsetConflictError(
                    f"ingest_offsets {change.source} {change.day} is no longer at "
                    f"{change.old_offset}/{change.old_line}"
                )

    # -- rows -------------------------------------------------------------------------------------

    def _copy_bisect(
        self,
        connection: Connection,
        lines: Sequence[PreparedLine],
        counts: dict[str, int],
        batch: Batch,
        loaded: list[_Loaded],
    ) -> int:
        """Load ``lines``; returns how many ``COPY`` attempts were refused on the way.

        Every line whose rows loaded goes into ``loaded``, for the ``counts`` step."""
        if not lines:
            return 0
        attempt: dict[str, int] = {}
        try:
            with connection.begin_nested():
                _copy_lines(connection, lines, attempt)
        except _CopyFailedError as failed:
            if len(lines) == 1:
                self._salvage(connection, lines[0], failed, counts, batch, loaded)
                return 1
            middle = len(lines) // 2
            # the line responsible is logged by _salvage; this only traces the bisection
            log.debug("COPY rejected %d lines, narrowing it down", len(lines))
            return (
                1
                + self._copy_bisect(connection, lines[:middle], counts, batch, loaded)
                + self._copy_bisect(connection, lines[middle:], counts, batch, loaded)
            )
        for table, count in attempt.items():
            counts[table] = counts.get(table, 0) + count
        loaded.extend((line, True) for line in lines)
        return 0

    def _salvage(
        self,
        connection: Connection,
        line: PreparedLine,
        failed: _CopyFailedError,
        counts: dict[str, int],
        batch: Batch,
        loaded: list[_Loaded],
    ) -> None:
        """The line that ``COPY`` refuses: count it; keep its ``messages`` row if that loads."""
        pattern = PATTERN_COPY + failed.table
        detail = f"{type(failed.error).__name__}: {failed.error}"[:DETAIL_MAX]
        log.error(
            "%s %s line %d could not be loaded (%s)",
            line.stream.source,
            line.stream.day,
            line.line,
            detail,
        )
        batch.load_failures.append(
            (
                line.stream.source,
                line.stream.day,
                Failure(pattern, line.line, line.offset, line.received, detail),
            )
        )
        message = next((values for table, values in line.rows if table == "messages"), None)
        if message is None or failed.table == "messages":
            return
        alone = {**message, "parse_error": message.get("parse_error") or pattern}
        attempt: dict[str, int] = {}
        try:
            with connection.begin_nested():
                _copy_tables(connection, {"messages": [alone]}, attempt)
        except _CopyFailedError:
            return
        counts["messages"] = counts.get("messages", 0) + attempt.get("messages", 0)
        loaded.append((line, False))  # without its typed row: counted with tbl = ''

    # -- parse failures ---------------------------------------------------------------------------

    def _record_failures(self, connection: Connection, batch: Batch) -> int:
        aggregate: dict[tuple[str, date, str], _FailureAggregate] = {}
        now = utc_now()
        total = 0

        def add(source: str, day: date, failure: Failure) -> None:
            seen = failure.seen or now
            key = (source, day, failure.pattern)
            found = aggregate.get(key)
            if found is None:
                aggregate[key] = _FailureAggregate(
                    1, seen, seen, failure.line, failure.offset, failure.detail
                )
            else:
                found.count += 1
                found.first_seen = min(found.first_seen, seen)
                found.last_seen = max(found.last_seen, seen)
                found.error = failure.detail or found.error

        for line in batch.lines:
            for failure in line.failures:
                add(line.stream.source, line.stream.day, failure)
                total += 1
        for source, day, failure in batch.load_failures:
            add(source, day, failure)
            total += 1
        if not aggregate:
            return 0
        rows = [
            {
                "source": source,
                "day": day,
                "pattern": pattern,
                "count": value.count,
                "first_seen": value.first_seen,
                "last_seen": value.last_seen,
                "example_line": value.example_line,
                "example_offset": value.example_offset,
                "error": value.error,
            }
            for (source, day, pattern), value in sorted(aggregate.items())
        ]
        statement = insert(ParseFailure).values(rows)
        new = statement.excluded
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["source", "day", "pattern"],
                set_={
                    "count": ParseFailure.count + new.count,
                    "first_seen": func.least(ParseFailure.first_seen, new.first_seen),
                    "last_seen": func.greatest(ParseFailure.last_seen, new.last_seen),
                    "error": func.coalesce(new.error, ParseFailure.error),
                },
            )
        )
        return total

    # -- sources and the new-source alert ---------------------------------------------------------

    def _update_sources(
        self, connection: Connection, seen: Iterable[SourceSeen]
    ) -> tuple[str, ...]:
        created: list[str] = []
        for source in sorted(seen, key=lambda s: s.name):
            inserted = connection.execute(
                insert(Source)
                .values(
                    name=source.name,
                    type=source.type,
                    address=source.address,
                    expected=source.expected,
                    heartbeat_minutes=source.heartbeat_minutes,
                    unknown=source.unknown,
                    first_seen=source.first_seen,
                    last_seen=source.last_seen,
                )
                .on_conflict_do_nothing(index_elements=["name"])
                .returning(Source.name)
            ).scalar_one_or_none()
            if inserted is not None:
                created.append(source.name)
                if source.unknown:
                    self._new_source_alert(connection, source)
                continue
            values: dict[str, Any] = {"unknown": source.unknown}
            if source.first_seen is not None and source.last_seen is not None:
                values["first_seen"] = func.least(Source.first_seen, source.first_seen)
                values["last_seen"] = func.greatest(Source.last_seen, source.last_seen)
            if source.address is not None:
                values["address"] = source.address
            if source.configured:
                values.update(
                    type=source.type,
                    expected=source.expected,
                    heartbeat_minutes=source.heartbeat_minutes,
                )
            connection.execute(update(Source).where(Source.name == source.name).values(**values))
        return tuple(created)

    def _new_source_alert(self, connection: Connection, source: SourceSeen) -> None:
        """Alert ``new_source`` once, when ingest first records an ``unknown-<ip>`` sender."""
        address = source.address or source.name.removeprefix("unknown-")
        connection.execute(
            insert(Alert)
            .values(
                rule=NEW_SOURCE_RULE,
                severity="medium",
                title=f"Yeni log kaynağı: {address}",
                details={
                    "source": source.name,
                    "address": source.address,
                    "detected_type": source.type,
                    "first_seen": (
                        isoformat_site(source.first_seen, "seconds") if source.first_seen else None
                    ),
                    "title_en": f"New log source: {address}",
                },
                dedup_key=f"{NEW_SOURCE_RULE}:{source.name}",
            )
            .on_conflict_do_nothing(
                index_elements=["dedup_key"],
                index_where=text("state = 'open' AND dedup_key IS NOT NULL"),
            )
        )


def _count_keys(loaded: Iterable[_Loaded]) -> Counter[CountKey]:
    """The ``search_counts`` keys of the loaded lines: ``count_key`` of the ``messages`` row and,
    when it loaded, of the typed row (``prepare`` puts the ``messages`` row first; a line has at
    most one typed row).

    ``count_key`` costs a few microseconds a line, a measurable share of a line's whole load, so
    the lines are first grouped by everything it reads — the UTC hour of the device time, the
    file, type and subtype, the typed table, its action as written and the /24 of its client
    address — and the key is made once per group. Lines of one group have one key; two groups
    may share one (``Blocked`` and ``blocked``), the counter adds them.
    """
    groups: dict[tuple[Any, ...], list[Any]] = {}
    for line, typed_loaded in loaded:
        rows = line.rows
        if not rows or rows[0][0] != "messages":
            continue  # no messages row (rawlog.invalid, copy.messages): in no search, no count
        message = rows[0][1]
        table, typed = rows[1] if typed_loaded and len(rows) > 1 else (None, None)
        action = client = None
        if typed is not None:
            action = typed.get("action")
            column = CLIENT_IP.get(table or "")
            client = None if column is None else net_of(typed.get(column))
        group = (
            (message["time"] - _EPOCH) // _HOUR,  # exact: no float rounding at an hour's end
            message["source"],
            message["raw_day"],
            message["type"],
            message.get("subtype"),
            table,
            action,
            client,
        )
        found = groups.get(group)
        if found is None:
            groups[group] = [message, table, typed, 1]
        else:
            found[3] += 1
    keys: Counter[CountKey] = Counter()
    for message, table, typed, lines in groups.values():
        keys[count_key(message, table, typed)] += lines
    return keys


def _copy_lines(
    connection: Connection, lines: Sequence[PreparedLine], counts: dict[str, int]
) -> None:
    tables: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        for table, values in line.rows:
            tables.setdefault(table, []).append(values)
    _copy_tables(connection, tables, counts)


def _copy_tables(
    connection: Connection, tables: dict[str, list[dict[str, Any]]], counts: dict[str, int]
) -> None:
    """``COPY`` per table (``messages`` first) and per column set of its rows.

    Rows may leave out columns (``fg_traffic.is_interim``, ``extra``): PostgreSQL fills in the
    server default only for a column the ``COPY`` does not name, so naming the union of all
    rows' columns would send NULL for them and fail on NOT NULL. Rows with the same keys go in
    one ``COPY`` (a parser normally emits one key set per table, so this is one ``COPY``).
    Data errors become :class:`_CopyFailedError`.
    """
    for table in sorted(tables, key=lambda name: (name != "messages", name)):
        groups: dict[frozenset[str], list[dict[str, Any]]] = {}
        for row in tables[table]:
            groups.setdefault(frozenset(row), []).append(row)
        for rows in groups.values():
            try:
                counts[table] = counts.get(table, 0) + copy_rows(
                    connection, table, rows, columns=list(rows[0])
                )
            except DATA_ERRORS as exc:
                raise _CopyFailedError(table, exc) from exc


def is_transient(error: BaseException) -> bool:
    """Database unavailable or not ready (retry later) rather than a bug in this program."""
    return isinstance(error, DBAPIError)
