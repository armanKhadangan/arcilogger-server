"""Where lines are prepared: in the service process, or in a pool of worker processes.

Preparing a line (raw format, parser, ``check_row``) is pure CPU work and takes most of the
ingest time (measured in docs/INGEST.md); loading (``COPY``) is cheap in comparison. With
``workers = 0`` everything runs in the service process. With ``workers = N`` (default 2) chunks
of lines are prepared by N processes (``spawn``, as on Windows) while the service process reads
files and loads the database; results are used **in read order**, so offsets and exactly-once
are unchanged.

A chunk keeps its raw bytes until it is resolved: if a worker dies (or the pool breaks, e.g. a
console Ctrl+C reached the workers first, an antivirus or an out-of-memory kill), the chunk is
simply prepared in the service process. A broken pool is **replaced**: after a wait that starts
at ``backoff`` seconds and doubles up to ``max_backoff`` (reset after :data:`HEALTHY_AFTER` of
good work), a new pool is created; meanwhile chunks are prepared in the service process. The
status file shows the configured and the active workers, the restarts and the last error. A
worker ends itself when the service process is gone (killed, crashed), so no orphan stays
behind.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing.connection import wait
from typing import Final

from arcilogger_ingest.prepare import PreparedChunk, StreamInfo, prepare_chunk

log = logging.getLogger("arcilogger.ingest")

#: First wait before a broken pool is replaced, and the longest wait (seconds).
DEFAULT_BACKOFF: Final = 5.0
MAX_BACKOFF: Final = 300.0
#: A pool that worked this long resets the wait to ``backoff``.
HEALTHY_AFTER: Final = 600.0


def _ignore_stop_signals() -> None:
    """Workers leave stopping to the service process (it finishes or rolls back the batch).

    Only the console signals are ignored. On POSIX ``SIGTERM`` stays deliverable: when one
    worker dies, CPython terminates the others with ``SIGTERM`` and waits for them while holding
    the pool's lock — an ignored ``SIGTERM`` would block the service for good.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)


def _exit_with_parent() -> None:
    """End this worker when the service process is gone (killed, crashed, service wrapper
    timeout): a pool worker holds both ends of its task queue, so it would otherwise wait for
    work for ever as an orphan. Watches the parent's process sentinel (Windows and POSIX)."""
    parent = multiprocessing.parent_process()
    if parent is None:  # pragma: no cover - only called in pool workers
        return

    def watch() -> None:
        wait([parent.sentinel])
        os._exit(0)

    threading.Thread(target=watch, name="arcilogger-parent-watch", daemon=True).start()


def _worker_init() -> None:
    """Initializer of every parse worker."""
    _ignore_stop_signals()
    _exit_with_parent()


class PendingChunk:
    """A chunk being prepared; :meth:`result` returns it in any case."""

    def __init__(
        self,
        stream: StreamInfo,
        offset: int,
        line_count: int,
        data: bytes,
        future: Future[PreparedChunk] | None,
    ) -> None:
        self.stream = stream
        self.offset = offset
        self.line_count = line_count
        self.data = data
        self._future = future
        self._result: PreparedChunk | None = None

    def result(self) -> PreparedChunk:
        if self._result is None:
            if self._future is not None:
                try:
                    self._result = self._future.result()
                except (BrokenProcessPool, OSError, EOFError) as exc:
                    log.warning("parse worker failed (%s); preparing the chunk here", exc)
            if self._result is None:
                self._result = prepare_chunk(self.stream, self.offset, self.line_count, self.data)
            self.data = b""
            self._future = None
        return self._result


class Preparer:
    def __init__(
        self,
        workers: int = 0,
        *,
        backoff: float = DEFAULT_BACKOFF,
        max_backoff: float = MAX_BACKOFF,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workers = workers
        #: How often a broken pool was replaced, and why it broke the last time.
        self.restarts = 0
        self.last_error: str | None = None
        self._first_backoff = backoff
        self._backoff = backoff
        self._max_backoff = max_backoff
        self._clock = clock
        self._retry_at = 0.0
        self._created_at = 0.0
        self._closed = False
        self._pool: ProcessPoolExecutor | None = None
        if workers > 0:
            self._start_pool()

    @property
    def active_workers(self) -> int:
        """Worker processes in use now (0 while preparing in the service process)."""
        return self.workers if self._pool is not None else 0

    def status(self) -> dict[str, int | str | None]:
        return {
            "configured": self.workers,
            "active": self.active_workers,
            "restarts": self.restarts,
            "last_error": self.last_error,
        }

    def _start_pool(self) -> bool:
        try:
            self._pool = ProcessPoolExecutor(
                max_workers=self.workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_worker_init,
            )
        except (OSError, RuntimeError) as exc:  # pragma: no cover - resources exhausted
            self._broken(exc)
            return False
        self._created_at = self._clock()
        return True

    def _broken(self, exc: BaseException) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        self.last_error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        self._retry_at = self._clock() + self._backoff
        log.warning(
            "parse pool unavailable (%s); preparing in the service process, a new pool in %.0f s",
            self.last_error,
            self._backoff,
        )
        self._backoff = min(self._backoff * 2, self._max_backoff)

    def _revive(self) -> None:
        if self.workers <= 0 or self._closed or self._pool is not None:
            return
        if self._clock() < self._retry_at:
            return
        if self._start_pool():
            self.restarts += 1
            log.warning(
                "parse pool recreated (%d workers, restart %d)", self.workers, self.restarts
            )

    def submit(self, stream: StreamInfo, offset: int, line_count: int, data: bytes) -> PendingChunk:
        self._revive()
        future: Future[PreparedChunk] | None = None
        if self._pool is not None:
            try:
                future = self._pool.submit(prepare_chunk, stream, offset, line_count, data)
            except (BrokenProcessPool, RuntimeError) as exc:
                self._broken(exc)
            else:
                if self._clock() - self._created_at >= HEALTHY_AFTER:
                    self._backoff = self._first_backoff
        pending = PendingChunk(stream, offset, line_count, data, future)
        if future is None:
            pending.result()
        return pending

    def close(self) -> None:
        self._closed = True  # never recreated after closing
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
