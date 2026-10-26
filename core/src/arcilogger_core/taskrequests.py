"""Signed task requests of the browser administration, as the LOCAL SERVICE tasks take them
(ADR-0014; contract 2.4.5, 2.4.6, 2.9).

The administration agent hands an evidence action to the task that runs it every night, under
the same identity: "Şimdi mühürle" to the Seal task (``state\\requests\\seal\\<id>.json``), the
credit query, the TSA test and the replica test to the Requests task
(``state\\requests\\tasks\\<id>.json``). Each file is a request signed with the agent's key
(:mod:`arcilogger_core.admin.envelope`); the Event Log entry that wakes the task carries
nothing, the signature is the authority. :func:`take` returns the requests a task may run,
oldest first, each at most once:

1. a file ``<id>.json`` of at most 64 KiB (more is not read);
2. the envelope verifies with an agent key of ``admin\\keys\\keys.json`` in the folder of the
   task's fixed ``--config`` path (Day 0 note D0-2), never one ``secrets_dir`` points at;
3. the payload names the file's id and a kind of this queue, and it has not expired;
4. the ledger ``seen\\`` has no entry for it; the entry is written (and flushed) before the
   request is returned, so the same file never runs twice.

A file that fails a step is deleted and logged: a forged, damaged, expired or replayed request
never runs. Nothing of an unverified file is trusted, not even the id in its name, so no result
is written for it and the agent reports ``task_timeout``. :func:`finish` writes the result of a
request that ran (``results\\<id>.json``, :class:`~arcilogger_core.admin.contract.TaskResult`:
codes, texts and facts, never a secret, a token or a file path) and deletes the request;
:func:`clean` removes ledger entries, results and left-over request files after 7 days, longer
than any request is valid.

The channel never stops a task: the Seal task seals with or without it. A folder that cannot be
listed, a ``keys.json`` that cannot be read for a moment or a ledger entry that cannot be written
is logged, and the request waits for the next run (it expires on its own); a result that cannot
be written is logged too (the agent reports ``task_timeout``). None of these functions raises an
``OSError``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from arcilogger_core.admin.contract import (
    MAX_TASK_REQUEST_BYTES,
    RESULT_DAYS,
    SEEN_DAYS,
    ActionResult,
    KeysFile,
    TaskRequest,
    TaskResult,
    check_id,
    dump_document,
    task_queue,
)
from arcilogger_core.admin.envelope import EnvelopeError, open_task_request
from arcilogger_core.admin.keys import KeysError, load_keys
from arcilogger_core.admin.messages import MESSAGES
from arcilogger_core.fsutil import atomic_write_bytes, fsync_dir
from arcilogger_core.layout import Layout, RequestQueue
from arcilogger_core.timeutil import isoformat_site, utc_now

log = logging.getLogger("arcilogger.taskrequests")

SUFFIX = ".json"


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # locked for a moment (Windows): the next run deletes it
        log.warning("could not delete %s: %s", path.name, exc)


def _files(folder: Path) -> list[Path]:
    try:
        return sorted(path for path in folder.iterdir() if path.suffix == SUFFIX and path.is_file())
    except FileNotFoundError:
        return []
    except OSError as exc:  # not a folder, or no access: the task goes on without requests
        log.error("task requests in %s not readable: %s", folder.name, exc)
        return []


def _read(path: Path) -> bytes:
    """At most one byte more than a request may have (the envelope refuses the size)."""
    with path.open("rb") as handle:
        return handle.read(MAX_TASK_REQUEST_BYTES + 1)


def _verified(path: Path, queue: RequestQueue, keys: KeysFile, now: datetime) -> TaskRequest | None:
    """Steps 1-3 of the module docstring; None for a file that must not run."""
    try:
        check_id(path.stem)
        data = _read(path)
        request = open_task_request(data, keys, now=now)
    except EnvelopeError as exc:
        log.warning("task request %s refused (%s): %s", path.name, exc.code, exc.reason)
        return None
    except ValueError:
        log.warning("%s is not named like a task request", path.name)
        return None
    if request.id != path.stem:
        log.warning("task request %s names another id (%s)", path.name, request.id)
        return None
    if task_queue(request.kind) != queue:
        log.warning("task request %s (%s) is not for the %s queue", path.name, request.kind, queue)
        return None
    return request


def _first_sight(layout: Layout, request_id: str) -> bool:
    """Write the ledger entry ``seen\\<id>``; False when it was there already (a replay)."""
    folder = layout.request_seen
    folder.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(folder / request_id, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
    fsync_dir(folder)
    return True


def _unreadable_for_now(exc: KeysError) -> bool:
    """``keys.json`` is there but cannot be read at this moment (locked while ``keys init``
    rewrites it during an upgrade, a sharing violation): unlike a missing or invalid file, the
    requests can still be verified by a later run."""
    cause = exc.__cause__
    return isinstance(cause, OSError) and not isinstance(cause, FileNotFoundError)


def take(
    layout: Layout, queue: RequestQueue, keys_dir: Path, *, now: datetime | None = None
) -> list[TaskRequest]:
    """The valid requests of ``queue``, oldest first, each entered in the ledger (see the module
    docstring). ``keys_dir``: ``admin\\keys`` of the installation folder."""
    files = _files(layout.request_dir(queue))
    if not files:
        return []
    now = now or utc_now()
    try:
        keys = load_keys(keys_dir)
    except KeysError as exc:
        if _unreadable_for_now(exc):
            log.error("task requests cannot be verified now, %d kept: %s", len(files), exc)
            return []
        log.error("task requests cannot be verified, %d deleted: %s", len(files), exc)
        for path in files:
            _remove(path)
        return []
    taken: list[TaskRequest] = []
    for path in files:
        try:
            request = _verified(path, queue, keys, now)
        except OSError as exc:  # being written or locked: the next run reads it again
            log.warning("task request %s not readable now: %s", path.name, exc)
            continue
        if request is None:
            _remove(path)
            continue
        try:
            first = _first_sight(layout, request.id)
        except OSError as exc:  # the ledger cannot be written: run it only once it can
            log.error("task request %s kept, its ledger entry not written: %s", request.id, exc)
            continue
        if not first:
            log.warning("task request %s was taken before; the repeat is ignored", request.id)
            _remove(path)
            continue
        log.info("task request %s (%s, for %s) taken", request.id, request.kind, request.origin)
        taken.append(request)
    return sorted(taken, key=lambda request: (request.issued_at, request.id))


def task_result(
    request: TaskRequest,
    *,
    started_at: datetime,
    code: str | None = None,
    result: ActionResult | None = None,
    texts: tuple[str, str] | None = None,
    now: datetime | None = None,
) -> TaskResult:
    """The result of ``request``: ``ok`` without a ``code`` (a code of
    :mod:`arcilogger_core.admin.messages`). ``texts``: Turkish and English of what happened,
    by default those of the code."""
    if texts is None and code is not None:
        texts = MESSAGES[code]
    return TaskResult.model_validate(
        {
            "id": request.id,
            "kind": request.kind,
            "ok": code is None,
            "code": code,
            "message_tr": texts[0] if texts else None,
            "message_en": texts[1] if texts else None,
            "result": result,
            "started_at": isoformat_site(started_at, "seconds"),
            "finished_at": isoformat_site(now or utc_now(), "seconds"),
        }
    )


def finish(layout: Layout, request: TaskRequest, result: TaskResult) -> None:
    """Write ``results\\<id>.json``, then delete the request (the ledger keeps its id). A result
    that cannot be written is logged: the request has run, the agent reports ``task_timeout``."""
    try:
        atomic_write_bytes(layout.request_results / f"{request.id}{SUFFIX}", dump_document(result))
    except OSError as exc:
        log.error("result of task request %s not written: %s", request.id, exc)
    _remove(layout.request_dir(task_queue(request.kind)) / f"{request.id}{SUFFIX}")


def _entries(layout: Layout) -> Iterator[tuple[Path, int]]:
    """Every file :func:`clean` looks at, with the days it is kept."""
    folders = (
        (layout.request_seen, SEEN_DAYS),
        (layout.request_results, RESULT_DAYS),
        (layout.request_dir("seal"), SEEN_DAYS),
        (layout.request_dir("tasks"), SEEN_DAYS),
    )
    for folder, days in folders:
        try:
            for path in folder.iterdir():
                if path.is_file():
                    yield path, days
        except OSError:  # missing, not a folder or not readable: nothing to clean there
            continue


def clean(layout: Layout, *, now: datetime | None = None) -> int:
    """Delete ledger entries and results older than 7 days (and request files left that long);
    returns how many."""
    cutoff = (now or utc_now()).timestamp()
    removed = 0
    for path, days in _entries(layout):
        try:
            old = path.stat().st_mtime < cutoff - timedelta(days=days).total_seconds()
        except OSError:
            continue
        if old:
            _remove(path)
            removed += 1
    return removed
