"""The page position of a search: an opaque, strictly parsed cursor (contract 2.6.3).

A cursor is base64url (without padding) of one of::

    {"v":1,"k":"last","t":"<UTC ISO µs>","b":<rank>,"i":<id>}   # after the last row delivered
    {"v":1,"k":"frontier","t":"<UTC ISO µs>"}                   # the time budget ran out at t

:func:`decode` accepts exactly these (an aware time inside the search's ``[from, to]``, a rank
among the plan's branches, an id ≥ 0, no other key) and answers anything else with 422
``search_cursor_invalid``. Every part is bound as a parameter, so a changed cursor can only
choose which rows **of the same audited search** are shown.

Rows are ordered ``time DESC, b ASC, id DESC`` (``b`` the branch rank). After a ``last`` cursor
``(t, R, i)`` each branch continues below its own bound (:func:`bounds`):

========  ===================  ======================================================
rank r    condition            why
========  ===================  ======================================================
r < R     ``time < t``         its rows at ``time = t`` came before branch R's: delivered
r = R     ``(time, id) < (t, i)``  continue inside the branch
r > R     ``time <= t``        its rows at ``time = t`` come after branch R's: still due
========  ===================  ======================================================

After a ``frontier`` cursor ``T``: ``time < T`` on every branch.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from arcilogger_api.errors import ApiError
from arcilogger_api.search.messages import search_error

VERSION: Final = 1
#: Longer texts are no cursor this module wrote.
MAX_CHARS: Final = 256
MAX_ID: Final = 2**63 - 1
_KEYS: Final = {"last": {"v", "k", "t", "b", "i"}, "frontier": {"v", "k", "t"}}

Kind = Literal["last", "frontier"]


@dataclass(frozen=True, slots=True)
class Cursor:
    """Where the next page starts (see the module text)."""

    kind: Kind
    time: datetime
    rank: int | None = None
    id: int | None = None

    @classmethod
    def last(cls, time: datetime, rank: int, id_: int) -> Cursor:
        return cls("last", time, rank, id_)

    @classmethod
    def frontier(cls, time: datetime) -> Cursor:
        return cls("frontier", time)


@dataclass(frozen=True, slots=True)
class Bound:
    """The newest rows a branch may still deliver: ``time < time``, ``time <= time``
    (``inclusive``) or ``(time, id) < (time, before_id)``."""

    time: datetime
    inclusive: bool = False
    before_id: int | None = None


def utc_text(time: datetime) -> str:
    """A time as written in cursors and row keys (UTC, microseconds)."""
    return time.astimezone(UTC).isoformat(timespec="microseconds")


def encode(cursor: Cursor) -> str:
    data: dict[str, Any] = {"v": VERSION, "k": cursor.kind, "t": utc_text(cursor.time)}
    if cursor.kind == "last":
        data["b"] = cursor.rank
        data["i"] = cursor.id
    text = json.dumps(data, separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode("ascii")).decode("ascii").rstrip("=")


def _invalid() -> ApiError:
    return search_error("search_cursor_invalid")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def decode(text: str, *, start: datetime, end: datetime, ranks: Iterable[int]) -> Cursor:
    """The cursor of ``text`` for a search of ``[start, end)`` with these branch ranks; 422
    ``search_cursor_invalid`` for anything else."""
    if not text or len(text) > MAX_CHARS or not text.isascii():
        raise _invalid()
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        data = json.loads(raw.decode("ascii"), object_pairs_hook=_no_duplicates)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise _invalid() from None
    if not isinstance(data, dict) or data.get("k") not in _KEYS:
        raise _invalid()
    kind: Kind = data["k"]
    if set(data) != _KEYS[kind] or not _integer(data["v"]) or data["v"] != VERSION:
        raise _invalid()
    if not isinstance(data["t"], str):
        raise _invalid()
    try:
        time = datetime.fromisoformat(data["t"])
    except ValueError:
        raise _invalid() from None
    if time.tzinfo is None or time.utcoffset() is None or not start <= time <= end:
        raise _invalid()
    if kind == "frontier":
        return Cursor.frontier(time)
    rank, id_ = data["b"], data["i"]
    if not _integer(rank) or rank not in set(ranks):
        raise _invalid()
    if not _integer(id_) or not 0 <= id_ <= MAX_ID:
        raise _invalid()
    return Cursor.last(time, rank, id_)


def bounds(cursor: Cursor | None, end: datetime, ranks: Iterable[int]) -> dict[int, Bound]:
    """The upper bound of every branch for the first window of a page."""
    if cursor is None:
        return {rank: Bound(end) for rank in ranks}
    if cursor.kind == "frontier":
        return {rank: Bound(cursor.time) for rank in ranks}
    last = cursor.rank if cursor.rank is not None else 0
    found: dict[int, Bound] = {}
    for rank in ranks:
        if rank < last:
            found[rank] = Bound(cursor.time)
        elif rank == last:
            found[rank] = Bound(cursor.time, before_id=cursor.id)
        else:
            found[rank] = Bound(cursor.time, inclusive=True)
    return found
