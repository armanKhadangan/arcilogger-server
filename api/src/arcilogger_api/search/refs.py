"""Evidence from a search: the raw references of its rows (contract 2.13).

"Kanıt paketine aktar" calls ``POST /search/refs``: the whole search up to ``limit`` lines (at
most 10,000, the evidence extract's maximum), read by the same window scan as its pages, in
keyset chunks, under ``search.stats_budget_ms``. The answer says whether the line limit cut the
list (``truncated``), whether the time budget did (``complete: false``) and which days of it are
not sealed yet (``unsealed_days``: ``POST /evidence`` would refuse them with
``lines_not_sealed``); it is audited (``search.refs``) with the count and the SHA-256 of the
list as canonical JSON (RFC 8785), which :func:`refs_sha256` recomputes. The evidence package
itself stays ``POST /evidence`` with its own permission, re-authentication and audit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from time import monotonic
from typing import Any, Final

from sqlalchemy.orm import Session

from arcilogger_api.search.cursor import Cursor
from arcilogger_api.search.planner import Plan
from arcilogger_api.search.windows import Clock, SearchTimeoutError, WindowHook, run_page
from arcilogger_core.canonical import canonical_json
from arcilogger_core.layout import Layout

#: Rows asked for per page while collecting.
CHUNK: Final = 2000


@dataclass(frozen=True)
class Refs:
    refs: list[dict[str, Any]]
    truncated: bool
    complete: bool


def collect(
    session: Session,
    plan: Plan,
    start: datetime,
    end: datetime,
    limit: int,
    budget_seconds: float,
    *,
    clock: Clock = monotonic,
    hook: WindowHook | None = None,
) -> Refs:
    """Up to ``limit`` raw references of the search, newest first."""
    deadline = clock() + budget_seconds
    refs: list[dict[str, Any]] = []
    cursor: Cursor | None = None
    while True:
        left = deadline - clock()
        if left <= 0:
            return Refs(refs, False, False)
        want = min(CHUNK, limit + 1 - len(refs))  # one more: is there anything beyond limit?
        try:
            page = run_page(session, plan, start, end, cursor, want, left, clock=clock, hook=hook)
        except SearchTimeoutError:
            return Refs(refs, False, False)
        refs += [
            {
                "source": row["source"],
                "day": row["raw_day"],
                "line": int(row["raw_line"]),
                "offset": int(row["raw_offset"]),
            }
            for row in page.rows
        ]
        if len(refs) > limit:
            return Refs(refs[:limit], True, True)
        if page.next_cursor is None:
            return Refs(refs, False, True)
        cursor = page.next_cursor


def refs_sha256(refs: Sequence[dict[str, Any]]) -> str:
    """SHA-256 of the list as canonical JSON (days as ``YYYY-MM-DD``)."""
    plain = [{**ref, "day": ref["day"].isoformat()} for ref in refs]
    return hashlib.sha256(canonical_json(plain)).hexdigest()


def unsealed_days(layout: Layout, refs: Sequence[dict[str, Any]]) -> list[date]:
    """Days of ``refs`` with a file that is not in the vault yet."""
    files = {(ref["day"], ref["source"]) for ref in refs}
    return sorted(
        {day for day, source in files if not (layout.vault_day(day) / f"{source}.log.gz").is_file()}
    )
