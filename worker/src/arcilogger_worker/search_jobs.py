"""``search.counts_backfill``: count the files loaded before the search summary table existed.

The summary table ``search_counts`` (ADR-0015, :mod:`arcilogger_db.searchcounts`) is written by
the ingest loader in the transaction of the rows, from revision ``0016_search`` on. The migration
marks every file that already had loaded lines (``ingest_offsets.counts_from_line``): ingest counts
the lines it loads from then on, the lines before were never counted. This job counts such a file
again from its rows (:func:`~arcilogger_db.searchcounts.rebuild_stream`, which deletes the file's
keys first, so the lines ingest counted meanwhile are not counted twice) and clears the mark.

* Every 10 minutes; it starts no new file after :data:`BACKFILL_BUDGET` (60 s), so after an
  upgrade of a large database the files are counted over several runs, newest day first (recent
  statistics are wanted first).
* Only **finished** files (``finished_at`` set: the day is sealed and every line is loaded):
  ingest never writes a finished file again, so a recount cannot race the loader. A file still
  being loaded waits (``waiting`` in the details); until every file of a time range is counted
  the search statistics count that range directly (``searchcounts.counts_complete``).
* One transaction per file: the file's ``ingest_offsets`` row is locked first (``FOR UPDATE``) and
  read again, so a second worker or ``arcilogger-db search-counts rebuild`` running at the same
  time waits and then finds the file done instead of counting it twice.
* A fresh installation has no marked file: the run is recorded as ``skipped``.

The job belongs to 5651 Temel and never asks the licence (docs/LICENSING.md section 2.3).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import Engine, func, select, update

from arcilogger_db.models import IngestOffset
from arcilogger_db.searchcounts import rebuild_stream

log = logging.getLogger("arcilogger_worker.search")

#: ``search.counts_backfill`` starts no new file after this long (the file in hand is finished).
BACKFILL_BUDGET: Final = timedelta(seconds=60)


@dataclass
class BackfillReport:
    #: The files counted: ``{source, day, lines, keys}``.
    files: list[dict[str, Any]] = field(default_factory=list)
    #: Finished files left for the next run (the time budget was used up).
    left: int = 0
    #: Marked files still being loaded: counted once they are finished.
    waiting: int = 0

    @property
    def more(self) -> bool:
        return self.left > 0

    def details(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "files": self.files,
            "lines": sum(item["lines"] for item in self.files),
            "waiting": self.waiting,
        }
        if self.more:
            found["more"] = True
            found["left"] = self.left
        return found


def backfill_counts(engine: Engine, *, budget: timedelta = BACKFILL_BUDGET) -> BackfillReport:
    """``search.counts_backfill``: count the finished files marked ``counts_from_line``."""
    report = BackfillReport()
    started = time.monotonic()
    marked = IngestOffset.counts_from_line.is_not(None)
    with engine.connect() as connection:
        todo = connection.execute(
            select(IngestOffset.source, IngestOffset.day)
            .where(marked, IngestOffset.finished_at.is_not(None))
            .order_by(IngestOffset.day.desc(), IngestOffset.source)
        ).all()
        report.waiting = int(
            connection.scalar(
                select(func.count()).where(marked, IngestOffset.finished_at.is_(None))
            )
            or 0
        )
    for index, (source, day) in enumerate(todo):
        if report.files and time.monotonic() - started > budget.total_seconds():
            report.left = len(todo) - index
            break
        with engine.begin() as connection:
            locked = connection.execute(
                select(IngestOffset.source)
                .where(
                    IngestOffset.source == source,
                    IngestOffset.day == day,
                    marked,
                    IngestOffset.finished_at.is_not(None),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if locked is None:  # counted meanwhile (another worker, the command line)
                continue
            result = rebuild_stream(connection, source, day)
            connection.execute(
                update(IngestOffset)
                .where(IngestOffset.source == source, IngestOffset.day == day)
                .values(counts_from_line=None)
            )
        report.files.append(
            {"source": source, "day": day.isoformat(), "lines": result.lines, "keys": result.keys}
        )
        log.info(
            "search counts of %s %s: %d lines, %d keys", source, day, result.lines, result.keys
        )
    return report
