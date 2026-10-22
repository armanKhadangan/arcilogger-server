"""``alerts.import``: every ``state/alerts.jsonl`` line once — across restarts, torn last lines,
replaced files and broken lines; dedup keys keep one active alert per problem."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.alerts import Severity, raise_alert
from arcilogger_core.layout import Layout
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, Setting
from arcilogger_db.settings import SYSTEM_AUTHOR
from arcilogger_worker.alerts.importer import (
    MAX_LINE_BYTES,
    OFFSET_KEY,
    import_alerts,
    import_key,
)
from arcilogger_worker.service import Worker, default_jobs

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


def alerts(session: Session) -> list[Alert]:
    session.expire_all()
    return list(session.scalars(select(Alert).order_by(Alert.id)))


def line(rule: str, severity: str = "high", message: str = "m", **details: Any) -> bytes:
    entry = {
        "time": "2026-10-19T11:59:00+03:00",
        "rule": rule,
        "severity": severity,
        "message": message,
        "details": details,
    }
    return json.dumps(entry, ensure_ascii=False).encode("utf-8") + b"\n"


def append(layout: Layout, data: bytes) -> None:
    layout.alerts.parent.mkdir(parents=True, exist_ok=True)
    with layout.alerts.open("ab") as handle:
        handle.write(data)


def test_each_line_once_across_runs_and_restarts(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    raise_alert(layout.alerts, "R20-spool", "critical", "Ham log yazılamıyor", {"source": "x"})
    raise_alert(layout.alerts, "R20-spool-torn", "medium", "yarım satır", {"source": "x"})
    first = import_alerts(sessions, layout, now=NOW)
    assert (first.lines, first.created, first.problems) == (2, 2, [])
    assert import_alerts(sessions, layout, now=NOW).lines == 0  # nothing new
    rows = alerts(db_session)
    assert [(a.rule, a.severity, a.state) for a in rows] == [
        ("R20-spool", "critical", "open"),
        ("R20-spool-torn", "medium", "open"),
    ]
    assert rows[0].title == "Ham log yazılamıyor"
    assert rows[0].details["source"] == "x"
    assert rows[0].details["origin"] == {"file": "state/alerts.jsonl", "offset": 0}
    assert rows[0].created_at == NOW
    # A restart (a new session factory, a new worker) continues at the stored offset.
    raise_alert(layout.alerts, "R20-spool", "critical", "again", {"source": "y"})
    later = import_alerts(sessions, layout, now=NOW + timedelta(minutes=1))
    assert later.lines == 1
    assert len(alerts(db_session)) == 3
    offset = db_session.scalars(select(Setting).where(Setting.key == OFFSET_KEY)).one()
    assert offset.value["offset"] == layout.alerts.stat().st_size
    assert offset.updated_by == SYSTEM_AUTHOR


def test_a_torn_last_line_waits_for_its_newline(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    complete = line("R20-spool", source="a")
    torn = line("R20-spool", source="b")
    append(layout, complete + torn[:20])
    report = import_alerts(sessions, layout, now=NOW)
    assert (report.lines, report.partial, report.offset) == (1, True, len(complete))
    assert import_alerts(sessions, layout, now=NOW).lines == 0  # still waiting
    append(layout, torn[20:])
    report = import_alerts(sessions, layout, now=NOW)
    assert (report.lines, report.partial) == (1, False)
    assert [a.details["source"] for a in alerts(db_session)] == ["a", "b"]


def test_an_alert_after_a_torn_tail_from_a_power_cut_is_imported(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """A power cut left NUL bytes without a newline (the size grew, the data never arrived)."""
    raise_alert(layout.alerts, "R20-spool", "critical", "before", {"source": "a"})
    end = layout.alerts.stat().st_size
    append(layout, b"\x00" * 40)
    report = import_alerts(sessions, layout, now=NOW)
    assert (report.lines, report.partial, report.offset) == (1, True, end)  # waits for "\n"
    raise_alert(layout.alerts, "R20-spool-torn", "medium", "after", {"source": "fortigate"})
    report = import_alerts(sessions, layout, now=NOW)
    assert (report.lines, report.partial, report.problems) == (
        1,
        False,
        [f"offset {end}: not an alert"],  # the fragment alone, skipped
    )
    rows = alerts(db_session)
    assert [a.title for a in rows] == ["before", "after"]
    assert rows[1].details["origin"]["offset"] == end + 41


@pytest.mark.parametrize("torn", [b"\x00" * 40, b"half"])
def test_an_alert_appended_straight_after_a_fragment_is_still_found(
    layout: Layout,
    sessions: sessionmaker[Session],
    db_session: Session,
    tmp_path: Path,
    torn: bytes,
) -> None:
    """An older ``raise_alert`` wrote the next alert right after the torn bytes."""
    elsewhere = tmp_path / "elsewhere.jsonl"
    raise_alert(elsewhere, "R20-seal", "high", "half", {"day": "2026-10-18"})
    raise_alert(elsewhere, "R20-spool-torn", "medium", "after", {"source": "fortigate"})
    first, second = elsewhere.read_bytes().splitlines(keepends=True)
    fragment = first[:30] if torn == b"half" else torn  # half a line starts like an alert too
    complete = line("R20-spool", source="a")
    append(layout, complete + fragment + second + b"junk " + second[:-1] + b" junk\n")
    report = import_alerts(sessions, layout, now=NOW)
    assert report.lines == 2
    assert report.problems == [
        f"offset {len(complete)}: torn fragment of {len(fragment)} bytes skipped",
        f"offset {len(complete) + len(fragment) + len(second)}: not an alert",
    ]
    rows = alerts(db_session)
    assert [a.title for a in rows] == ["m", "after"]
    assert rows[1].details["origin"]["offset"] == len(complete) + len(fragment)


def test_crash_before_commit_imports_nothing_twice(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """The offset is written in the transaction of the alerts: no commit, neither."""
    append(layout, line("R20-spool", source="a") + line("R20-spool", source="b"))

    class CrashError(Exception):
        pass

    def crashing() -> Session:
        session = sessions()

        def commit() -> None:
            raise CrashError

        session.commit = commit  # type: ignore[method-assign]
        return session

    with pytest.raises(CrashError):
        import_alerts(crashing, layout, now=NOW)  # type: ignore[arg-type]
    assert alerts(db_session) == []
    assert import_alerts(sessions, layout, now=NOW).lines == 2
    assert import_alerts(sessions, layout, now=NOW).lines == 0
    assert len(alerts(db_session)) == 2


def test_broken_lines_are_skipped_and_never_block_the_file(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    append(
        layout,
        b"not json\n"
        + b'{"rule": 5}\n'
        + b'{"rule": "R20-spool", "details": {"x": NaN}}\n'
        + b"\n"
        + b"x" * (MAX_LINE_BYTES + 10)
        + b"\n"
        + line("R20-spool", severity="bogus", message="null \x00 byte", note="\x00")
        + b"\xff\xfe\n"
        + line("R20-spool-torn", source="ok"),
    )
    report = import_alerts(sessions, layout, now=NOW)
    assert report.lines == 2
    assert len(report.problems) == 5
    assert any("longer than" in problem for problem in report.problems)
    rows = alerts(db_session)
    assert [a.rule for a in rows] == ["R20-spool", "R20-spool-torn"]
    assert rows[0].severity == "medium"  # an unknown severity
    assert rows[0].details["severity_given"] == "bogus"
    assert "\x00" not in rows[0].title
    assert rows[0].details["note"] == "�"


def test_a_replaced_file_is_read_again_from_the_start(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    append(layout, line("R20-spool", source="a") + line("R20-spool", source="b"))
    assert import_alerts(sessions, layout, now=NOW).lines == 2
    layout.alerts.unlink()
    assert import_alerts(sessions, layout, now=NOW).lines == 0  # missing: nothing to do
    append(layout, line("R20-spool-torn", source="new"))  # shorter than the offset
    report = import_alerts(sessions, layout, now=NOW)
    assert (report.reset, report.lines) == (True, 1)
    # Same length, other first line: also a different file.
    layout.alerts.unlink()
    append(layout, line("R20-spool-torn", source="wen"))
    report = import_alerts(sessions, layout, now=NOW)
    assert (report.reset, report.lines) == (True, 1)
    assert [a.details["source"] for a in alerts(db_session)] == ["a", "b", "new", "wen"]


def test_repeated_problems_keep_one_active_alert(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """The Seal task retries a failed day every few minutes: one R20-seal alert per day."""
    for attempt in (1, 2, 3):
        severity: Severity = "critical" if attempt == 3 else "high"
        raise_alert(
            layout.alerts,
            "R20-seal",
            severity,
            f"Mühürleme başarısız (attempt {attempt})",
            {"day": "2026-10-18", "attempts": attempt},
        )
    raise_alert(layout.alerts, "R20-seal", "high", "other day", {"day": "2026-10-17"})
    raise_alert(layout.alerts, "R20-clock", "high", "Saat sapması 1500 ms", {"offset_us": 1})
    raise_alert(layout.alerts, "R20-clock", "high", "Saat sapması 1600 ms", {"offset_us": 2})
    report = import_alerts(sessions, layout, now=NOW)
    assert (report.lines, report.created, report.merged) == (6, 3, 3)
    rows = {a.dedup_key: a for a in alerts(db_session)}
    day = rows["R20-seal:2026-10-18"]
    assert day.title == "Mühürleme başarısız (attempt 1)"  # the first occurrence
    assert day.details["repeats"] == 2
    assert day.details["attempts"] == 3
    assert day.details["last_title"] == "Mühürleme başarısız (attempt 3)"
    assert day.severity == "critical"  # only ever goes up
    assert rows["R20-clock"].details["repeats"] == 1
    assert set(rows) == {"R20-seal:2026-10-18", "R20-seal:2026-10-17", "R20-clock"}
    # Once resolved, the same problem opens a new alert.
    rows["R20-clock"].state = "resolved"
    db_session.commit()
    raise_alert(layout.alerts, "R20-clock", "high", "Saat sapması 2000 ms", {"offset_us": 3})
    assert import_alerts(sessions, layout, now=NOW).created == 1


@pytest.mark.parametrize(
    ("rule", "details", "key"),
    [
        ("R20-seal", {"day": "2026-10-18", "attempts": 2}, "R20-seal:2026-10-18"),
        ("R20-seal", {"day": "nonsense"}, None),
        ("R20-seal-config", {"cause": "credentials"}, "R20-seal-config:credentials"),
        ("R20-seal-skip", {"key": "skip:2026-10-01"}, "R20-seal-skip:skip:2026-10-01"),
        ("R20-clock", {"offset_us": 5}, "R20-clock"),
        (
            "R20-clock",
            {"key": "stray:fortigate:2026-10-01"},
            "R20-clock:stray:fortigate:2026-10-01",
        ),
        ("R20-credit", {}, "R20-credit"),
        ("R20-verify", {"failed_days": []}, "R20-verify"),
        ("R20-replica", {"day": "2026-10-01"}, "R20-replica"),
        ("R20-backup", {}, "R20-backup"),
        ("new-log-source", {"address": "172.20.19.9"}, "new-log-source:172.20.19.9"),
        ("R20-spool", {"source": "fortigate"}, None),
        ("R20-retention", {}, None),
    ],
)
def test_import_keys(rule: str, details: dict[str, Any], key: str | None) -> None:
    assert import_key(rule, details) == key


def test_the_worker_imports_every_minute(
    layout: Layout, db_engine: Engine, db_session: Session
) -> None:
    (job,) = [job for job in default_jobs(60) if job.name == "alerts.import"]
    assert job.every == timedelta(minutes=1)
    raise_alert(layout.alerts, "R20-spool", "critical", "x", {})
    worker = Worker(engine=db_engine, layout=layout, jobs=[job], clock=lambda: NOW)
    assert worker.tick() == [("alerts.import", "ok")]
    assert [a.rule for a in alerts(db_session)] == ["R20-spool"]
