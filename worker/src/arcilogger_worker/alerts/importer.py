"""``alerts.import``: new lines of ``state/alerts.jsonl`` into the ``alerts`` table, each once.

The evidence tasks (sealing, verification, replica, retention, clock, credit) and the collector
run without the database and append their alerts to ``state/alerts.jsonl``
(:func:`arcilogger_core.alerts.raise_alert`, one JSON object per line). The browser
administration (ADR-0014) has two more files in the same format: the agent ArciAdmin's
``admin/status/alerts.jsonl`` and ApplySystem's ``admin/system/alerts.jsonl`` (:data:`ADMIN_FILES`,
read from the ``admin`` folder next to the worker's fixed ``--config`` path). Each file has its
own position and is read the same way. A file is only ever appended to, so a **byte offset** is
enough to know what was imported:

* The offset (the end of the last complete line imported) is stored in an internal setting
  (:data:`OFFSET_KEY`; the administration's files: the keys of :data:`ADMIN_FILES`) in the
  **same transaction** as the alerts of those lines, under an advisory lock. A crash before
  the commit leaves neither, after it both: every line is imported exactly once, across
  restarts and with two workers by mistake.
* Only complete lines (ending with ``\\n``) are read. A torn last line — the writer is in the
  middle of it, or a power cut left it — waits for its newline; the offset stays before it.
  After a power cut that newline comes from the next alert: ``raise_alert`` starts it on a fresh
  line, so the fragment (half a line, NUL bytes) becomes a line of its own, skipped and
  reported. An alert appended straight after a fragment (by an older version) is still found at
  the end of that line and imported (:func:`_after_fragment`).
* The setting also keeps the SHA-256 of the file's first line. A file that is shorter than the
  offset, or whose first line changed, was replaced (restored from a backup, deleted by hand):
  it is read again from the start and the run reports ``reset`` (lines already imported may then
  be imported twice — the dedup keys below keep that to one active alert per problem).
* A line that is not an alert (not JSON, no rule) is skipped and reported; it never blocks the
  lines after it.

Each line becomes one alert, or — when an active alert with the same dedup key exists
(:func:`import_key`) — is counted on it (``details.repeats``): the Seal task retries a failed day
every few minutes, but that is one problem (``R20-seal:<day>``) until the day is sealed. The line's
``time`` stays the alert's ``time``; ``details`` keeps the line's details plus ``origin``
(file and offset).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.admin.contract import AdminLayout
from arcilogger_core.layout import Layout
from arcilogger_db.models import ALERT_SEVERITIES
from arcilogger_db.settings import SYSTEM_AUTHOR, get_setting, set_setting
from arcilogger_worker.alerts.store import TITLE_MAX, open_alert

log = logging.getLogger("arcilogger_worker.alerts")

#: Internal setting (author ``:system``, not editable): ``{"offset": int, "head": sha256}``.
OFFSET_KEY: Final = "worker.alerts_import"
#: pg_advisory_xact_lock key ("ARCIALIM" as a signed 64-bit integer).
IMPORT_LOCK_KEY: Final = int.from_bytes(b"ARCIALIM", "big") - 2**63
SOURCE: Final = "state/alerts.jsonl"


@dataclass(frozen=True)
class AlertFile:
    """One alert file: where it is, its name in ``details.origin.file`` and the internal
    setting that keeps its position."""

    path: Path
    source: str
    offset_key: str


#: The administration's alert files: the agent's and ApplySystem's (source, position setting).
ADMIN_FILES: Final = (
    ("admin/status/alerts.jsonl", "worker.alerts_import.admin"),
    ("admin/system/alerts.jsonl", "worker.alerts_import.system"),
)


def alert_files(layout: Layout, admin: AdminLayout | None = None) -> list[AlertFile]:
    """``state/alerts.jsonl``, then the administration's files when there is an ``admin``
    folder."""
    files = [AlertFile(layout.alerts, SOURCE, OFFSET_KEY)]
    if admin is not None:
        (agent_source, agent_key), (system_source, system_key) = ADMIN_FILES
        files.append(AlertFile(admin.alerts, agent_source, agent_key))
        files.append(AlertFile(admin.system_alerts, system_source, system_key))
    return files


#: Lines per transaction, and transactions per run (the rest waits for the next minute).
BATCH_LINES: Final = 500
MAX_BATCHES: Final = 20
#: The first line's hash covers at most this many bytes.
HEAD_BYTES: Final = 4096
#: A longer line is not an alert (``raise_alert`` writes a few hundred bytes): skipped.
MAX_LINE_BYTES: Final = 1024 * 1024
#: Longest message kept in ``details.message`` when it does not fit the title.
MESSAGE_MAX: Final = 4000
RULE: Final = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: How every ``raise_alert`` line begins (sorted keys): where an alert after a fragment starts.
ENTRY_START: Final = b'{"details": '
#: Places on a broken line where an alert after a torn fragment is looked for.
FRAGMENT_TRIES: Final = 8
#: Rules that keep one active alert, whatever their details.
ONE_ACTIVE: Final = ("R20-clock", "R20-credit", "R20-verify", "R20-replica", "R20-backup")
#: Rules that keep one active alert per sender address.
PER_ADDRESS: Final = ("new-log-source", "wrong-transport")


@dataclass
class AlertImportReport:
    lines: int = 0
    created: int = 0
    merged: int = 0
    offset: int = 0
    #: Bytes after the last complete line (a line still being written).
    partial: bool = False
    #: The file was replaced and read again from the start.
    reset: bool = False
    problems: list[str] = field(default_factory=list)
    #: The administration's files read in the same run (by source), each with its own report;
    #: the fields above are those of ``state/alerts.jsonl``.
    files: dict[str, AlertImportReport] = field(default_factory=dict)

    def details(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "lines": self.lines,
            "created": self.created,
            "merged": self.merged,
            "offset": self.offset,
            "partial": self.partial,
            "reset": self.reset,
            "problems": self.problems[:50],
            "problem_count": len(self.problems),
        }
        if self.files:
            result["files"] = {source: report.details() for source, report in self.files.items()}
        return result


@dataclass(frozen=True)
class Position:
    offset: int = 0
    head: str = ""


@dataclass
class Batch:
    #: ``(offset of the line, line without its newline)``; an oversized line is ``b""`` with
    #: its offset in ``skipped``.
    lines: list[tuple[int, bytes]] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    end: int = 0
    head: str = ""
    reset: bool = False
    #: Stopped at the line limit: more complete lines may follow.
    more: bool = False
    partial: bool = False


def read_position(session: Session, key: str = OFFSET_KEY) -> Position:
    value = get_setting(session, key, None)
    if not isinstance(value, Mapping):
        return Position()
    offset, head = value.get("offset"), value.get("head")
    if type(offset) is not int or offset < 0:
        return Position()
    return Position(offset, head if isinstance(head, str) else "")


def _head(handle: Any) -> str:
    """SHA-256 of the first line (or its first :data:`HEAD_BYTES`); ``""`` while incomplete."""
    first: bytes = handle.readline(HEAD_BYTES)
    if first.endswith(b"\n") or len(first) == HEAD_BYTES:
        return hashlib.sha256(first).hexdigest()
    return ""


def read_batch(path: Path, position: Position, limit: int = BATCH_LINES) -> Batch:
    """Complete lines after ``position`` (at most ``limit``); ``FileNotFoundError`` if missing."""
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        batch = Batch(head=_head(handle))
        offset = position.offset
        if offset > size or (offset > 0 and position.head and batch.head != position.head):
            offset, batch.reset = 0, True
        handle.seek(offset)
        while len(batch.lines) + len(batch.skipped) < limit:
            line = handle.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            if line.endswith(b"\n"):
                batch.lines.append((offset, line[:-1]))
                offset += len(line)
                continue
            if len(line) <= MAX_LINE_BYTES:
                batch.partial = True  # the writer is in the middle of it: wait for "\n"
                break
            length = _rest_of_line(handle)
            if length is None:
                batch.partial = True
                break
            batch.skipped.append(offset)
            offset += len(line) + length
        else:
            batch.more = True
        batch.end = offset
    return batch


def _rest_of_line(handle: Any) -> int | None:
    """Bytes up to and including the next newline (``None``: the file ends first)."""
    total = 0
    while True:
        chunk: bytes = handle.readline(MAX_LINE_BYTES)
        if not chunk:
            return None
        total += len(chunk)
        if chunk.endswith(b"\n"):
            return total


def _day(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def import_key(rule: str, facts: Mapping[str, Any]) -> str | None:
    """The dedup key of an imported alert (``None``: every line is its own alert)."""
    if rule == "R20-seal":
        day = _day(facts.get("day"))
        return f"R20-seal:{day}" if day else None
    if rule == "R20-seal-config":
        cause = facts.get("cause")
        return f"R20-seal-config:{cause}" if isinstance(cause, str) and RULE.match(cause) else rule
    key = facts.get("key")
    if isinstance(key, str) and key:
        return f"{rule}:{key[:200]}"
    if rule in ONE_ACTIVE:
        return rule
    if rule in PER_ADDRESS:
        address = facts.get("address")
        return f"{rule}:{address}" if isinstance(address, str) and address else rule
    return None


def _clean(value: Any, depth: int = 0) -> Any:
    """JSON as PostgreSQL ``jsonb`` stores it: no NUL characters, a bounded depth."""
    if depth > 10:
        return str(value)[:200]
    if isinstance(value, str):
        return value.replace("\x00", "�")
    if isinstance(value, dict):
        return {str(k).replace("\x00", "�"): _clean(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(item, depth + 1) for item in value]
    return value


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"not JSON: {name}")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _parse(line: bytes) -> dict[str, Any] | None:
    try:
        entry = json.loads(line.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(entry, dict):
        return None
    rule = entry.get("rule")
    if not isinstance(rule, str) or not RULE.match(rule):
        return None
    cleaned: dict[str, Any] = _clean(entry)
    return cleaned


def _after_fragment(line: bytes) -> tuple[int, dict[str, Any]] | None:
    """The complete alert at the end of a line that is not one, and where it starts: a power cut
    left NUL bytes or half a line, and the next alert was appended straight after it."""
    start = len(line) - len(line.lstrip(b"\x00"))
    if 0 < start < len(line) and (entry := _parse(line[start:])) is not None:
        return start, entry
    start = line.find(ENTRY_START, 1)
    for _ in range(FRAGMENT_TRIES):
        if start < 0:
            break
        if (entry := _parse(line[start:])) is not None:
            return start, entry
        start = line.find(ENTRY_START, start + 1)
    return None


def import_line(
    session: Session,
    offset: int,
    line: bytes,
    report: AlertImportReport,
    *,
    now: datetime,
    source: str = SOURCE,
) -> None:
    """One line of the alert file ``source`` (inside the caller's transaction, in a
    savepoint)."""
    if not line.strip():
        return
    entry = _parse(line)
    if entry is None:
        found = _after_fragment(line)
        if found is None:
            report.problems.append(f"offset {offset}: not an alert")
            return
        start, entry = found
        report.problems.append(f"offset {offset}: torn fragment of {start} bytes skipped")
        offset += start
    rule = str(entry["rule"])
    severity = entry.get("severity")
    raw = entry.get("details")
    facts: dict[str, Any] = raw if isinstance(raw, dict) else {}
    message = str(entry.get("message") or rule)
    details: dict[str, Any] = {**facts, "origin": {"file": source, "offset": offset}}
    if severity not in ALERT_SEVERITIES:
        details["severity_given"] = str(severity)[:40]
        severity = "medium"
    if len(message) > TITLE_MAX:
        details["message"] = message[:MESSAGE_MAX]
    try:
        with session.begin_nested():
            opened = open_alert(
                session,
                rule=rule,
                severity=str(severity),
                title=message,
                details=details,
                key=import_key(rule, facts),
                time=_time(entry.get("time")) or now,
                repeat=True,
                now=now,
            )
    except DataError as exc:  # a value PostgreSQL refuses: skip the line, never block the file
        report.problems.append(f"offset {offset}: {type(exc.orig).__name__}")
        return
    report.lines += 1
    if opened.created:
        report.created += 1
    else:
        report.merged += 1


def import_alert_file(
    sessions: sessionmaker[Session], file: AlertFile, *, now: datetime
) -> AlertImportReport:
    """Import every complete line of ``file`` after its stored offset (see the module
    docstring)."""
    report = AlertImportReport()
    for _ in range(MAX_BATCHES):
        with sessions() as session:
            session.execute(select(func.pg_advisory_xact_lock(IMPORT_LOCK_KEY)))
            position = read_position(session, file.offset_key)
            try:
                batch = read_batch(file.path, position)
            except FileNotFoundError:
                report.offset = position.offset
                return report
            except OSError as exc:
                report.problems.append(f"{file.path.name}: {type(exc).__name__}")
                log.warning("cannot read %s: %s", file.path, exc)
                return report
            if batch.reset:
                report.reset = True
                log.warning(
                    "%s was replaced (shorter than offset %d or another first line): read again "
                    "from the start",
                    file.path,
                    position.offset,
                )
            for offset in batch.skipped:
                report.problems.append(f"offset {offset}: line longer than {MAX_LINE_BYTES} bytes")
            for offset, line in batch.lines:
                import_line(session, offset, line, report, now=now, source=file.source)
            if (batch.end, batch.head) != (position.offset, position.head):
                set_setting(
                    session,
                    file.offset_key,
                    {"offset": batch.end, "head": batch.head},
                    updated_by=SYSTEM_AUTHOR,
                )
            session.commit()
        report.offset, report.partial = batch.end, batch.partial
        if not batch.more:
            break
    if report.problems:
        log.warning("alerts import (%s): %s", file.source, "; ".join(report.problems[:10]))
    return report


def import_alerts(
    sessions: sessionmaker[Session],
    layout: Layout,
    *,
    now: datetime,
    admin: AdminLayout | None = None,
) -> AlertImportReport:
    """``state/alerts.jsonl``, then the administration's alert files (``admin``: the
    ``admin`` folder of the installation; None: there is none)."""
    main, *others = alert_files(layout, admin)
    report = import_alert_file(sessions, main, now=now)
    for file in others:
        report.files[file.source] = import_alert_file(sessions, file, now=now)
    return report
