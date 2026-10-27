"""One page of a search: the time-budgeted window scan and the rows it shows (contract 2.6.1).

No query can run away and compete with ingest: a page scans **windows** of device time newest
first — 1 hour, doubling (2, 4, 8 h …) while a window answered in less than a quarter of the
budget left, clipped to ``from`` — each one the plan's statement with ``LIMIT`` = rows still
missing + 1 under :func:`~arcilogger_api.routers.lookup.statement_deadline` (the time left as
PostgreSQL ``statement_timeout``). It stops when the page is full, ``from`` is reached or
``search.request_budget_ms`` is used up:

* ``complete`` is true when ``from`` was reached or the page filled; ``next_cursor`` is
  ``last`` (the page filled), ``frontier`` (the budget ran out: every row newer than it was
  shown) or ``None`` (the end);
* a window whose statement timed out does not count; it runs in a savepoint, so the request's
  read-only snapshot (``REPEATABLE READ``) survives it;
* after a ``last`` cursor the first window always runs, even when the cursor's time is ``from``
  itself (the rows still due at that instant: later branches, lower ids), and a budget that ran
  out before any window gives the same cursor back;
* if the **first** window of a request times out it is halved, down to 5 minutes — each try of
  it gets half the time left, so a smaller one can still run; when even 5 minutes time out the
  request fails with :class:`SearchTimeoutError` (504 ``search_timeout``).

:func:`page_rows` then fills the page in: the typed fields of a ``messages``-driven page and the
``messages`` fields of a typed page, one keyed query per table (``sql.typed_rows``,
``sql.rows_by_reference``), on a page under a deadline of their own (:data:`FILL_SECONDS`), in the
export under the time its budget has left (a timeout there cuts the file).
"""

from __future__ import annotations

import ipaddress
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Final

from sqlalchemy import RowMapping, Select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_api.routers.lookup import QUERY_CANCELED, LookupTimeoutError, statement_deadline
from arcilogger_api.search import sql
from arcilogger_api.search.cursor import Bound, Cursor, bounds, utc_text
from arcilogger_api.search.fields import FIELDS, PROTOCOL_NAMES, SiteNetwork
from arcilogger_api.search.planner import Plan
from arcilogger_db.kinds import KINDS, ascii_lower, kind_of, kind_of_row
from arcilogger_db.models import TIME_SERIES_TABLES

#: The first window of a page, and the smallest one a timed-out first window is halved to.
FIRST_WINDOW: Final = timedelta(hours=1)
MIN_WINDOW: Final = timedelta(minutes=5)
#: A window that answered within this share of the time left doubles the next one.
GROW_SHARE: Final = 0.25
#: The fill-in queries of a page get this deadline of their own (keyed lookups; not the export).
FILL_SECONDS: Final = 1.0
#: ``SearchRow.text`` is at most this long (the export has the whole ``messages.text``).
ROW_TEXT_MAX: Final = 500
#: Characters shown as ``\\uXXXX`` (as ``rawlines.display_text`` does).
_UNSAFE: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn"})

Clock = Callable[[], float]
#: The seconds a fill-in query may take, read just before it runs (the export's budget left).
Seconds = Callable[[], float]
#: Called inside each window's deadline before its statement (tests make windows slow).
WindowHook = Callable[[Session, datetime, datetime], None]


class SearchTimeoutError(RuntimeError):
    """Not even the shortest window (5 minutes) could be scanned in the budget."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"no window finished within {seconds} s")
        self.seconds = seconds


class WindowTimeoutError(RuntimeError):
    """One statement used up its deadline (the savepoint was rolled back)."""


def is_timeout(exc: DBAPIError) -> bool:
    """PostgreSQL ``query_canceled``: a statement timeout."""
    original = exc.orig
    return QUERY_CANCELED in (
        getattr(original, "sqlstate", None),
        getattr(original, "pgcode", None),
    )


def run_statement(
    session: Session,
    statement: Select[Any],
    seconds: float,
    *,
    hook: WindowHook | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> Sequence[RowMapping]:
    """The rows of ``statement`` within ``seconds``, in a savepoint; :class:`WindowTimeoutError`
    when the deadline passes (the transaction and its snapshot go on)."""
    savepoint = session.begin_nested()
    try:
        with statement_deadline(session, seconds):
            if hook is not None and window is not None:
                hook(session, *window)
            rows = session.execute(statement).mappings().all()
    except (LookupTimeoutError, DBAPIError) as exc:
        if isinstance(exc, DBAPIError) and not is_timeout(exc):
            raise
        savepoint.rollback()
        raise WindowTimeoutError(str(exc)) from None
    savepoint.commit()
    return rows


@dataclass(frozen=True, slots=True)
class Scan:
    """How far a page's scan got (``SearchPage.scan``)."""

    complete: bool
    scanned_from: datetime
    scanned_to: datetime
    windows: int
    elapsed_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "scanned_from": self.scanned_from,
            "scanned_to": self.scanned_to,
            "windows": self.windows,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True, slots=True)
class Page:
    rows: list[RowMapping]
    next_cursor: Cursor | None
    scan: Scan


def run_page(
    session: Session,
    plan: Plan,
    start: datetime,
    end: datetime,
    cursor: Cursor | None,
    limit: int,
    budget_seconds: float,
    *,
    clock: Clock = monotonic,
    hook: WindowHook | None = None,
) -> Page:
    """The next ``limit`` rows of ``[start, end)`` after ``cursor`` (see the module text)."""
    began = clock()
    deadline = began + budget_seconds
    ranks = sorted(plan.ranks)
    upper = end if cursor is None else cursor.time
    limits: dict[int, Bound] = bounds(cursor, end, ranks)
    rows: list[RowMapping] = []
    size = FIRST_WINDOW
    windows = 0
    budget_left = True
    # Rows at the cursor's own time may still be due, also when that time is ``from``.
    due = cursor is not None and cursor.kind == "last"
    while len(rows) < limit and (upper > start or (due and windows == 0)):
        left = deadline - clock()
        if left <= 0:
            budget_left = False
            break
        lower = max(start, upper - size)
        first = windows == 0
        seconds = left / 2 if first and size > MIN_WINDOW else left
        statement = sql.page_statement(plan, lower, limits, limit - len(rows) + 1)
        asked = clock()
        try:
            found = run_statement(session, statement, seconds, hook=hook, window=(lower, upper))
        except WindowTimeoutError:
            if not first:
                budget_left = False
                break
            if size <= MIN_WINDOW:
                raise SearchTimeoutError(budget_seconds) from None
            size = max(MIN_WINDOW, size / 2)
            continue
        windows += 1
        rows += found
        upper = lower
        limits = {rank: Bound(lower) for rank in ranks}
        if clock() - asked < GROW_SHARE * (deadline - clock()):
            size *= 2
    elapsed = int((clock() - began) * 1000)
    scanned_to = end if cursor is None else cursor.time
    if len(rows) >= limit and (len(rows) > limit or upper > start):
        rows = rows[:limit]
        last = rows[-1]
        page_cursor = Cursor.last(last["time"], int(last["b"]), int(last["id"]))
        scan = Scan(True, last["time"], scanned_to, windows, elapsed)
        return Page(rows, page_cursor, scan)
    if not budget_left:
        # Without a window the cursor's own bounds still hold; a frontier would skip its rows.
        resume = cursor if windows == 0 and cursor is not None else Cursor.frontier(upper)
        return Page(rows, resume, Scan(False, upper, scanned_to, windows, elapsed))
    return Page(rows, None, Scan(True, start, scanned_to, windows, elapsed))


# -- the rows of a page ---------------------------------------------------------------------------


def display(text: str, limit: int = ROW_TEXT_MAX) -> str:
    """``text`` safe to show (control, bidi and other invisible characters as ``\\uXXXX``, like
    ``rawlines.display_text``), cut to ``limit`` characters."""
    out: list[str] = []
    length = 0
    for char in text:
        piece = f"\\u{ord(char):04x}" if unicodedata.category(char) in _UNSAFE else char
        if length + len(piece) > limit:
            break
        out.append(piece)
        length += len(piece)
    return "".join(out)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _first(table: str, row: Mapping[Any, Any], field: str) -> Any:
    for name in FIELDS[field].columns.get(table, ()):
        value = row.get(name)
        if value is not None:
            return value
    return None


def vlan_of(site: SiteNetwork, address: Any) -> int | None:
    """The VLAN whose subnet holds ``address``, the most specific one when subnets overlap (as
    the statistics' ``SiteLabels``; ``None``: none, or no address)."""
    if address is None:
        return None
    try:
        ip = ipaddress.ip_interface(str(address)).ip
    except ValueError:
        return None
    holding = [
        (-subnet.prefixlen, vlan_id)
        for vlan_id, subnet in site.vlans.items()
        if ip.version == subnet.version and ip in subnet
    ]
    return min(holding)[1] if holding else None


def summary(
    table: str | None,
    row: Mapping[Any, Any] | None,
    site: SiteNetwork,
    text_max: int = ROW_TEXT_MAX,
) -> dict[str, Any]:
    """``SearchRow.summary`` of a typed row (empty without one); texts display-safe, at most
    ``text_max`` characters."""
    if table is None or row is None:
        return {}
    found: dict[str, Any] = {}
    for key, field in (("srcip", "srcip"), ("dstip", "dstip"), ("natip", "natip"), ("mac", "mac")):
        found[key] = _text(_first(table, row, field))
    for key in ("srcport", "dstport", "natport"):
        found[key] = _first(table, row, key)
    for key, field in (
        ("action", "action"),
        ("domain", "domain"),
        ("url", "url"),
        ("app", "app"),
        ("category", "category"),
        ("user", "user"),
    ):
        value = _first(table, row, field)
        found[key] = None if value is None else display(str(value), text_max)
    proto = _first(table, row, "proto")
    if isinstance(proto, int):
        found["proto"] = PROTOCOL_NAMES.get(proto, str(proto))
    else:
        found["proto"] = None if proto is None else ascii_lower(str(proto))
    if table == "fg_traffic":
        name, number = row.get("policyname"), row.get("policyid")
        found["policy"] = display(str(name), text_max) if name is not None else _text(number)
    interfaces = [row.get(name) for name in FIELDS["interface"].columns.get(table, ())]
    shown = [display(str(value), text_max) for value in interfaces if value is not None]
    found["interface"] = " → ".join(shown) if shown else None
    found["vlan_id"] = vlan_of(site, found["srcip"])
    return {key: value for key, value in found.items() if value is not None}


RawKey = tuple[datetime, str, Any, int]
#: A row as the database returns it (``RowMapping``) or as a dict.
Fields = Mapping[Any, Any]


def _raw_key(row: Fields) -> RawKey:
    return (row["time"], row["source"], row["raw_day"], int(row["raw_line"]))


def _fetch(
    session: Session, statement: Select[Any], seconds: Seconds | None
) -> Sequence[RowMapping]:
    if seconds is not None:
        return run_statement(session, statement, seconds())  # the export: a timeout cuts the file
    try:
        return run_statement(session, statement, FILL_SECONDS)
    except WindowTimeoutError:
        return []  # the rows are still shown, without the fields of the other table


def _typed_of_messages(
    session: Session, rows: Sequence[RowMapping], seconds: Seconds | None
) -> dict[RawKey, tuple[str, RowMapping]]:
    """The typed row of each line of a ``messages``-driven page."""
    wanted: dict[str, list[RawKey]] = {}
    for row in rows:
        kind = KINDS[kind_of(row["type"], row["subtype"])]
        for table in kind.table_names:
            wanted.setdefault(table, []).append(_raw_key(row))
    found: dict[RawKey, tuple[str, RowMapping]] = {}
    for table in sorted(wanted, key=TIME_SERIES_TABLES.index):
        for typed in _fetch(session, sql.rows_by_reference(table, wanted[table]), seconds):
            found[_raw_key(typed)] = (table, typed)
    return found


def page_rows(
    session: Session,
    plan: Plan,
    rows: Sequence[RowMapping],
    site: SiteNetwork,
    *,
    text_max: int = ROW_TEXT_MAX,
    seconds: Seconds | None = None,
) -> list[dict[str, Any]]:
    """The ``SearchRow`` of each row of a page (``rows`` as :func:`run_page` returned them).

    On a page each fill-in query gets :data:`FILL_SECONDS` and one that times out leaves its rows
    without the other table's fields. The export passes ``text_max`` (the whole
    ``messages.text``) and ``seconds`` (its budget left): there a timeout raises
    :class:`WindowTimeoutError`, so a file is cut, never written with empty columns."""
    if not rows:
        return []
    if plan.driver == "messages":
        typed = _typed_of_messages(session, rows, seconds)
        return [
            _row("messages", row, row, typed.get(_raw_key(row)), site, text_max) for row in rows
        ]
    by_table: dict[str, list[tuple[datetime, int]]] = {}
    for row in rows:
        by_table.setdefault(TIME_SERIES_TABLES[row["b"]], []).append((row["time"], row["id"]))
    typed_rows: dict[tuple[str, datetime, int], RowMapping] = {}
    for table, keys in by_table.items():
        for found in _fetch(session, sql.typed_rows(table, keys), seconds):
            typed_rows[(table, found["time"], found["id"])] = found
    messages = {
        _raw_key(found): found
        for found in _fetch(session, sql.message_rows([_raw_key(row) for row in rows]), seconds)
    }
    result = []
    for row in rows:
        table = TIME_SERIES_TABLES[row["b"]]
        own = typed_rows.get((table, row["time"], row["id"]))
        pair = (table, own) if own else None
        result.append(_row(table, row, messages.get(_raw_key(row)), pair, site, text_max))
    return result


def _row(
    table: str,
    row: Fields,
    message: Fields | None,
    typed: tuple[str, Fields] | None,
    site: SiteNetwork,
    text_max: int,
) -> dict[str, Any]:
    typed_table, typed_row = typed if typed is not None else (None, None)
    if message is not None:
        kind = kind_of(message["type"], message["subtype"])
    else:
        kind = kind_of_row(table, (typed_row or {}).get("event")) or "other"
    text = message.get("text") if message is not None else None
    return {
        "key": f"{table}:{utc_text(row['time'])}:{row['id']}",
        "table": table,
        "time": row["time"],
        "time_received": row["time_received"],
        "source": row["source"],
        "sender": str(row["sender"]),
        "kind": kind,
        "type": message.get("type") if message is not None else None,
        "subtype": message.get("subtype") if message is not None else None,
        "summary": summary(typed_table, typed_row, site, text_max),
        "text": None if text is None else display(str(text), text_max),
        "raw": {
            "source": row["source"],
            "day": row["raw_day"],
            "line": int(row["raw_line"]),
            "offset": int(row["raw_offset"]),
        },
        "parse_error": message.get("parse_error") if message is not None else None,
    }
