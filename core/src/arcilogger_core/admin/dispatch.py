"""Task requests for the LOCAL SERVICE tasks, and their results (ADR-0014; contract 2.4.5, 2.4.6,
2.9).

An evidence action runs under the identity that runs it every night, never in the agent: the
agent writes a task request signed with its own key (``admin.envelope``, domain
``arcilogger.task-request/1``) into ``state\\requests\\seal\\`` (the Seal task) or
``state\\requests\\tasks\\`` (the Requests task) and wakes the task with an Event Log entry (4110,
4111). The event carries nothing: the signed file is the authority, and the task verifies it,
its expiry and its own ``seen`` ledger before it acts. The task writes
``state\\requests\\results\\<id>.json``; the agent folds it into the request's status and journal.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from arcilogger_core.admin.contract import (
    MAX_SEAL_REQUEST_VALIDITY,
    MAX_TASK_REQUEST_VALIDITY,
    DocumentError,
    Event,
    TaskKind,
    TaskParams,
    TaskRequest,
    TaskResult,
    new_id,
    parse_document,
    task_queue,
)
from arcilogger_core.admin.envelope import sign_task_request
from arcilogger_core.admin.keys import SigningKey
from arcilogger_core.fsutil import fsync_dir
from arcilogger_core.layout import Layout


def task_event(kind: TaskKind) -> Event:
    """The event that wakes the task of ``kind``."""
    return Event.SEAL_REQUESTED if kind == "seal" else Event.TASK_REQUESTED


def new_task_request(
    kind: TaskKind, *, origin: str, params: TaskParams, now: datetime
) -> TaskRequest:
    """A task request valid for 6 hours (sealing: a running Seal task ignores the wake-up, the
    next 5-minute run takes it) or 30 minutes (the other tasks)."""
    validity = MAX_SEAL_REQUEST_VALIDITY if kind == "seal" else MAX_TASK_REQUEST_VALIDITY
    return TaskRequest(
        id=new_id(),
        origin=origin,
        kind=kind,
        params=params,
        issued_at=now,
        expires_at=now + validity,
    )


def request_path(layout: Layout, request: TaskRequest) -> Path:
    return layout.request_dir(task_queue(request.kind)) / f"{request.id}.json"


def write_task_request(layout: Layout, request: TaskRequest, key: SigningKey) -> Path:
    """Sign ``request`` and put it into its queue (``.tmp``, fsync, rename: a task never sees a
    partial file)."""
    data = sign_task_request(request, key)
    target = request_path(layout, request)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(target)
    fsync_dir(target.parent)
    return target


def result_path(layout: Layout, task_id: str) -> Path:
    return layout.request_results / f"{task_id}.json"


def read_result(layout: Layout, task_id: str, kind: TaskKind) -> TaskResult | None:
    """The result of task request ``task_id``; None while there is none. A file that is not a
    result of this request and kind counts as none (the shared LOCAL SERVICE identity could
    write one: residual risk 3 of contract 2.18)."""
    try:
        data = result_path(layout, task_id).read_bytes()
    except OSError:
        return None
    try:
        result = parse_document(TaskResult, data)
    except DocumentError:
        return None
    if result.id != task_id or result.kind != kind:
        return None
    return result
