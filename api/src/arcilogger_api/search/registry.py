"""The searches of this API process: one audited search, many follow-ups (contract 2.6.2).

``POST /search`` runs the first page, writes the audit entry ``search.run`` and keeps the search
here under a random ``search_id`` (``secrets.token_urlsafe(16)``). Its pages, statistics,
evidence references and export take only that id, so every row, count and file a user sees
belongs to an audited search; the follow-ups are not audited again.

An entry is bound to its user **and** session (the token hash): another session's, another
user's, an evicted, an expired or an unknown id answers 409 ``search_expired`` and the web app
runs the search again — a new search, with a new ``search.run``. The TTL slides (30 minutes
after the last use); at most 20 entries per user and 1,000 in all (the least recently used
goes). In memory, in one Uvicorn worker (ADR-0001): lost on a restart, with the same answer.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from arcilogger_api.search.messages import search_error
from arcilogger_api.search.planner import Plan
from arcilogger_core.timeutil import utc_now

TTL: Final = timedelta(minutes=30)
PER_USER: Final = 20
TOTAL: Final = 1000
ID_BYTES: Final = 16


def new_search_id() -> str:
    return secrets.token_urlsafe(ID_BYTES)


@dataclass
class Entry:
    """One search: what ran, for whom, and when it was last used."""

    search_id: str
    user_id: int
    session_hash: str
    plan: Plan
    start: datetime
    end: datetime
    #: The page size of the first page (later pages default to it).
    limit: int
    saved_id: int | None
    created_at: datetime
    last_used: datetime = field(default_factory=utc_now)
    #: Notices found when the search ran (``vault_only_before``), repeated on every page.
    notices: tuple[dict[str, Any], ...] = ()

    @property
    def expires_at(self) -> datetime:
        return self.last_used + TTL


@dataclass
class SearchRegistry:
    clock: Callable[[], datetime] = utc_now
    ttl: timedelta = TTL
    per_user: int = PER_USER
    total: int = TOTAL
    _entries: OrderedDict[str, Entry] = field(default_factory=OrderedDict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def add(self, entry: Entry) -> Entry:
        """Keep ``entry`` (its ``last_used`` is now), dropping the least recently used ones
        beyond the limits."""
        with self._lock:
            now = self.clock()
            entry.last_used = now
            self._expire(now)
            own = [key for key, found in self._entries.items() if found.user_id == entry.user_id]
            for key in own[: max(0, len(own) - self.per_user + 1)]:
                del self._entries[key]
            while len(self._entries) >= self.total:
                self._entries.popitem(last=False)
            self._entries[entry.search_id] = entry
            return entry

    def get(self, search_id: str, user_id: int, session_hash: str) -> Entry:
        """The entry, used now; 409 ``search_expired`` unless it is this user's and session's."""
        with self._lock:
            now = self.clock()
            self._expire(now)
            entry = self._entries.get(search_id)
            if entry is None or entry.user_id != user_id or entry.session_hash != session_hash:
                raise search_error("search_expired")
            entry.last_used = now
            self._entries.move_to_end(search_id)
            return entry

    def _expire(self, now: datetime) -> None:
        stale = [key for key, entry in self._entries.items() if now - entry.last_used >= self.ttl]
        for key in stale:
            del self._entries[key]
