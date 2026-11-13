"""Every case of ``cases.py`` through the engine (Wave 6 contract §3, T-A1 and T-R): the case's
rows written as :mod:`.cases` describes, its settings stored, its slice evaluated by
:func:`~arcilogger_worker.rules.engine.evaluate_slice` and the alerts compared with what the case
expects: the keys, the counts, the values and evidence that ``/raw`` can open. A cooldown case
(:attr:`RuleCase.closed_before`) starts from a closed alert of every expected key.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.timeutil import site_day
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, AlertEvidence, Base, RuleState
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts.store import ACTIVE_STATES, CLOSED_STATES, open_alert, resolve
from arcilogger_worker.rules import registry
from arcilogger_worker.rules.engine import AFTER_CLOSE, RuleRun, evaluate_slice, json_value
from arcilogger_worker.rules.settings import effective, engine_settings
from arcilogger_worker.rules.texts import TEXTS

from .cases import CASES, SLICE_END, CaseRow, RuleCase

#: The senders of the case rows (fictional: the firewall and the router of Örnek A.Ş.).
FIREWALL, ROUTER = "172.20.0.1", "172.20.10.1"
#: Where a written row is: ``(table, row id)`` → ``(raw line, raw offset)``.
Written = dict[tuple[str, int | None], tuple[int | None, int | None]]


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=SLICE_END.date())
    return session_factory(db_engine)


def write_rows(session: Session, rows: tuple[CaseRow, ...]) -> Written:
    """The case's rows with what they do not name filled in (the writer of :mod:`.cases`)."""
    written: Written = {}
    line = 0
    for row in rows:
        table = Base.metadata.tables[row.table]
        for n in range(row.repeat):
            received = SLICE_END + row.received + timedelta(seconds=n)
            values: dict[str, Any] = dict(row.values)
            if row.table == "devices":
                values.setdefault("created_at", received)
                values.setdefault("first_seen", received + row.device_offset)
                values.setdefault("last_seen", received + row.device_offset)
                session.execute(insert(table).values(**values))
                written[("devices", None)] = (None, None)
                continue
            line += 1
            values.setdefault("source", "fortigate" if row.table.startswith("fg_") else "core-r1")
            values.setdefault("extra", {})
            row_id = session.execute(
                insert(table)
                .values(
                    time=received + row.device_offset,
                    time_received=received,
                    sender=FIREWALL if row.table.startswith("fg_") else ROUTER,
                    raw_day=site_day(received),
                    raw_line=line,
                    raw_offset=line * 1000,
                    **values,
                )
                .returning(table.c.id)
            ).scalar_one()
            written[(row.table, row_id)] = (line, line * 1000)
    return written


def device_times(rows: tuple[CaseRow, ...]) -> list[datetime]:
    return [
        SLICE_END + row.received + timedelta(seconds=n) + row.device_offset
        for row in rows
        for n in range(row.repeat)
    ]


def close_alerts(session: Session, case: RuleCase, cooldown: timedelta) -> None:
    """The closed alert of every expected key of a cooldown case; refuses a closed alert whose
    cooldown ends among the case's rows (which side of it a hit falls on is then unclear)."""
    assert case.closed_before is not None
    closed = SLICE_END - case.closed_before
    times = device_times(case.rows)
    assert closed + cooldown > max(times) or closed + cooldown <= min(times), (
        f"{case.rule}-{case.name}: the cooldown ends among the case's rows"
    )
    for expected in case.expected:
        opened = open_alert(
            session,
            rule=case.rule,
            severity="medium",
            title=TEXTS[case.rule].name_tr,
            details={"key": expected.key, "count": 1},
            key=f"{case.rule}:{expected.key}",
            time=closed,
            now=closed,
        )
        resolve(opened.alert, reason="case", now=closed + timedelta(seconds=1))


def alerts(session: Session, rule: str) -> list[Alert]:
    return list(session.scalars(select(Alert).where(Alert.rule == rule).order_by(Alert.id)))


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"{case.rule}-{case.name}")
def test_the_case(sessions: sessionmaker[Session], case: RuleCase) -> None:
    check(sessions, case)


@pytest.mark.parametrize(
    ("closed_before", "cooled"), [(timedelta(minutes=10), True), (timedelta(hours=3), False)]
)
def test_a_cooldown_case(
    sessions: sessionmaker[Session], closed_before: timedelta, cooled: bool
) -> None:
    """:attr:`RuleCase.closed_before` on a positive case with a cooldown of an hour or more:
    inside it the hits are counted on the closed alert, past it an alert opens."""
    case = next(
        case
        for case in CASES
        if case.positive
        and not case.overrides
        and registry.rule(case.rule).cooldown_seconds >= 3600
    )
    with_closed = replace(case, closed_before=closed_before)
    assert check(sessions, with_closed) is cooled


def check(sessions: sessionmaker[Session], case: RuleCase) -> bool:
    """Runs ``case`` and compares the alerts; whether its hits fell into a cooldown."""
    rule = registry.rule(case.rule)
    settings = effective(rule, dict(case.overrides), strict=True)
    cooldown = timedelta(seconds=settings.cooldown_seconds)
    with sessions() as session:
        for key, value in case.settings.items():
            set_setting(session, key, value, updated_by=":system")
        engine = engine_settings(session)
        start = SLICE_END - (
            engine.max_slice
            if case.slice_seconds is None
            else timedelta(seconds=case.slice_seconds)
        )
        written = write_rows(session, case.rows)
        if case.closed_before is not None:
            close_alerts(session, case, cooldown)
        session.add(RuleState(rule=rule.id, watermark=start, failures=0, alerts_opened=0))
        session.commit()
    run = RuleRun(rule.id)
    evaluate_slice(sessions, rule, settings, engine, (start, SLICE_END), SLICE_END, run)
    assert not run.failed

    with sessions() as session:
        found = alerts(session, rule.id)
        active = {alert.dedup_key: alert for alert in found if alert.state in ACTIVE_STATES}
        closed = {alert.dedup_key: alert for alert in found if alert.state in CLOSED_STATES}
        cooled = case.closed_before is not None and case.closed_before < cooldown
        expected_keys = {f"{rule.id}:{expected.key}" for expected in case.expected}
        assert set(active) == (set() if cooled else expected_keys)
        assert run.cooled == (len(case.expected) if cooled else 0)
        for expected in case.expected:
            key = f"{rule.id}:{expected.key}"
            if cooled:
                assert closed[key].details[AFTER_CLOSE]["count"] == expected.count
                continue
            alert = active[key]
            assert alert.details["count"] == expected.count
            assert alert.details["key"] == expected.key
            for name, value in expected.values.items():
                assert alert.details["values"][name] == json_value(value), name
            evidence = list(
                session.scalars(select(AlertEvidence).where(AlertEvidence.alert_id == alert.id))
            )
            if expected.evidence is not None:
                assert len(evidence) == min(expected.evidence, engine.evidence_max)
            for item in evidence:
                # The reference ``/raw`` opens is the written row's.
                assert written[(item.tbl, item.row_id)] == (item.raw_line, item.raw_offset)
    return cooled


def test_the_writer_keeps_a_named_source_and_dates_a_device(
    sessions: sessionmaker[Session],
) -> None:
    rows = (
        CaseRow("mt_config", timedelta(seconds=-30), {"source": "edge-r2", "user_name": "netops"}),
        CaseRow("fg_event", timedelta(seconds=-20), {"logid": "0100044546"}),
        CaseRow(
            "devices",
            timedelta(seconds=-10),
            {"mac": "00:00:5e:00:53:f0"},
            device_offset=timedelta(seconds=-90),
        ),
    )
    with sessions() as session:
        write_rows(session, rows)
        tables = Base.metadata.tables
        config = session.execute(select(tables["mt_config"].c.source)).scalar_one()
        event = session.execute(select(tables["fg_event"].c.source)).scalar_one()
        device = session.execute(
            select(tables["devices"].c.created_at, tables["devices"].c.first_seen)
        ).one()
    assert (config, event) == ("edge-r2", "fortigate")
    assert device == (SLICE_END - timedelta(seconds=10), SLICE_END - timedelta(seconds=100))
