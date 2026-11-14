"""The rule engine (Wave 6 contract §2.1.3, §2.1.5; tests T-A2, T-A3, T-A7) with a test rule over
``fg_traffic``: denied connections per source address in the window, at least ``threshold``.

T-A2: a slice is evaluated once; a failing slice rolls back and is evaluated again; no alert for
data older than the switch-on (D12). T-A3: dedup, cooldown and ``after_close``, the evidence cap,
the condition kind's auto-resolve. T-A7: list parameters reach SQL as bound parameters.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from ipaddress import ip_address
from typing import Any

import pytest
from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, AlertEvidence, FgTraffic, RuleState
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts.store import active_alert, change_state
from arcilogger_worker.rules import engine as rule_engine
from arcilogger_worker.rules.dryrun import DryRunTimeoutError, dry_run
from arcilogger_worker.rules.engine import FAILING_RULE, failing_key, run_rules
from arcilogger_worker.rules.model import EvidenceRef, Hit, Param, RuleContext, RuleDef
from arcilogger_worker.rules.settings import RuleSettingsError

NOW = datetime(2026, 11, 12, 9, 45, tzinfo=UTC)
#: The horizon at NOW with the default ``rules.lag_seconds`` (120).
HORIZON = NOW - timedelta(seconds=120)
TABLE = FgTraffic.__table__
#: An external scanner (RFC 5737) and a second one.
SCANNER, OTHER = "198.51.100.7", "203.0.113.9"


def _denied(session: Session, context: RuleContext) -> Iterable[Hit]:
    """The test rule: per ``srcip``, the rows with ``action`` in ``deny_actions`` received in the
    window, when at least ``threshold`` and at least one in the slice."""
    params = context.settings.params
    actions = list(params["deny_actions"])  # type: ignore[call-overload]
    match = (
        (TABLE.c.time >= context.time_low)
        & (TABLE.c.time <= context.time_high)
        & (TABLE.c.time_received > context.window_start)
        & (TABLE.c.time_received <= context.end)
        & TABLE.c.action.in_(actions)
    )
    groups = session.execute(
        select(TABLE.c.srcip, func.count(), func.max(TABLE.c.time_received))
        .where(match)
        .group_by(TABLE.c.srcip)
        .having(func.count() >= params["threshold"])
        .having(func.max(TABLE.c.time_received) > context.start)
    ).all()
    for srcip, count, newest in groups:
        rows = session.execute(
            select(
                TABLE.c.id,
                TABLE.c.time,
                TABLE.c.time_received,
                TABLE.c.source,
                TABLE.c.raw_day,
                TABLE.c.raw_line,
                TABLE.c.raw_offset,
            )
            .where(match & (TABLE.c.srcip == srcip))
            .order_by(TABLE.c.time_received, TABLE.c.id)
            .limit(20)
        ).all()
        evidence = [EvidenceRef("fg_traffic", *row) for row in rows]
        values = {"reason_tr": "eşik aşıldı", "reason_en": "over the threshold"}
        yield Hit(key=str(srcip), time=newest, count=count, values=values, evidence=evidence)


PARAMS = (
    Param("threshold", "int", 3, 1, 10_000, "Eşik", "Threshold"),
    Param("deny_actions", "strings", ("deny",), 1, 20, "Eylemler", "Actions"),
)
RULE = RuleDef(
    id="R02",
    kind="event",
    severity="medium",
    window_seconds=300,
    cooldown_seconds=3600,
    params=PARAMS,
    inputs=("fg_traffic",),
    evaluate=_denied,
)
CONDITION = RuleDef(
    id="R17",
    kind="condition",
    severity="high",
    window_seconds=300,
    cooldown_seconds=0,
    params=PARAMS,
    inputs=("fg_traffic",),
    evaluate=_denied,
    clear_seconds=600,
)


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=date(2026, 11, 12))
    return session_factory(db_engine)


def _rows(
    sessions: sessionmaker[Session],
    received: datetime,
    count: int,
    *,
    srcip: str = SCANNER,
    action: str = "deny",
) -> None:
    with sessions() as session:
        session.execute(
            insert(FgTraffic),
            [
                {
                    "time": received - timedelta(seconds=1),
                    "time_received": received + timedelta(milliseconds=n),
                    "source": "fw1",
                    "sender": "172.20.0.1",
                    "raw_day": date(2026, 11, 12),
                    "raw_line": 100 + n,
                    "raw_offset": 1000 * n,
                    "action": action,
                    "srcip": srcip,
                    "dstip": "172.20.41.17",
                    "dstport": 1000 + n,
                }
                for n in range(count)
            ],
        )
        session.commit()


def _setting(sessions: sessionmaker[Session], key: str, value: Any) -> None:
    with sessions() as session:
        set_setting(session, key, value, updated_by=":system")
        session.commit()


def _run(
    sessions: sessionmaker[Session], now: datetime = NOW, rule: RuleDef = RULE
) -> dict[str, Any]:
    return run_rules(sessions, now=now, rules=(rule,)).details()


def _state(sessions: sessionmaker[Session], rule: str = "R02") -> RuleState:
    with sessions() as session:
        state = session.get(RuleState, rule)
        assert state is not None
        session.expunge(state)
        return state


def _alerts(sessions: sessionmaker[Session], rule: str = "R02") -> list[Alert]:
    with sessions() as session:
        alerts = list(session.scalars(select(Alert).where(Alert.rule == rule).order_by(Alert.id)))
        session.expunge_all()
        return alerts


def _evidence(sessions: sessionmaker[Session], alert_id: int) -> list[AlertEvidence]:
    with sessions() as session:
        rows = list(
            session.scalars(
                select(AlertEvidence)
                .where(AlertEvidence.alert_id == alert_id)
                .order_by(AlertEvidence.n)
            )
        )
        session.expunge_all()
        return rows


def _no_audit(action: str, details: Any) -> None:
    return None


def _started(sessions: sessionmaker[Session], at: datetime = NOW) -> None:
    """The rule's first run: it starts at the horizon (D12)."""
    details = _run(sessions, at)
    assert details["started"] == ["R02"]
    assert details["slices"] == 0


# -- T-A2: watermark ----------------------------------------------------------------------------


def test_no_alert_for_data_older_than_the_switch_on(sessions: sessionmaker[Session]) -> None:
    _rows(sessions, HORIZON - timedelta(seconds=30), 10)  # before the first run
    _started(sessions)
    assert _state(sessions).watermark == HORIZON
    assert _alerts(sessions) == []
    later = NOW + timedelta(minutes=1)
    assert _run(sessions, later)["slices"] == 1
    assert _alerts(sessions) == []  # the old rows are in the window but none in the slice


def test_a_slice_is_evaluated_once(sessions: sessionmaker[Session]) -> None:
    _started(sessions)
    _rows(sessions, HORIZON + timedelta(seconds=20), 5)
    later = NOW + timedelta(minutes=1)
    details = _run(sessions, later)
    assert (details["slices"], details["opened"]) == (1, 1)
    (alert,) = _alerts(sessions)
    assert alert.dedup_key == f"R02:{SCANNER}"
    assert alert.title == f"Dış adres {SCANNER} 5 kez engellendi"
    assert alert.severity == "medium"
    assert alert.details["count"] == 5
    assert alert.details["title_en"] == f"External address {SCANNER} was blocked 5 times"
    assert alert.details["rule_kind"] == "event"
    assert alert.details["actions"] == {"email": False, "webhook": True}
    assert alert.details["format_verified"] is True
    assert alert.details["person_id"] is None
    assert [row.n for row in _evidence(sessions, alert.id)] == [1, 2, 3, 4, 5]
    assert _state(sessions).watermark == later - timedelta(seconds=120)
    # The same moment again: nothing left to evaluate, nothing counted twice.
    assert _run(sessions, later)["slices"] == 0
    assert _alerts(sessions)[0].details["count"] == 5


def test_slices_are_at_most_max_slice_seconds(sessions: sessionmaker[Session]) -> None:
    _started(sessions)
    _setting(sessions, "rules.max_slice_seconds", 60)
    details = _run(sessions, NOW + timedelta(minutes=5))
    assert details["slices"] == 5


def test_a_failing_slice_rolls_back_and_runs_again(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _setting(sessions, "rules.failures_alert", 2)
    _started(sessions)
    _rows(sessions, HORIZON + timedelta(seconds=20), 5)
    calls: list[int] = []

    def failing(session: Session, context: RuleContext) -> Iterable[Hit]:
        calls.append(1)
        hits = list(_denied(session, context))
        raise RuntimeError(f"broken {hits[0].key}")  # a message that names a value

    broken = RuleDef(**{**RULE.__dict__, "evaluate": failing})
    later = NOW + timedelta(minutes=1)
    assert _run(sessions, later, broken)["failed"] == ["R02"]
    state = _state(sessions)
    assert state.watermark == HORIZON  # rolled back
    assert (state.failures, state.last_error) == (1, "RuntimeError")
    assert _alerts(sessions) == []
    assert _run(sessions, later, broken)["failed"] == ["R02"]
    (failing_alert,) = _alerts(sessions, FAILING_RULE)
    assert failing_alert.dedup_key == failing_key("R02")
    assert failing_alert.severity == "high"
    assert failing_alert.title == "Güvenlik kuralı çalışamıyor: R02"
    assert failing_alert.details["title_en"] == "Security rule cannot run: R02"
    assert SCANNER not in str(failing_alert.details)
    # It works again: the slice is evaluated, the system alert resolves itself.
    details = _run(sessions, later)
    assert (details["slices"], details["opened"], details["resolved"]) == (1, 1, 1)
    assert _alerts(sessions, FAILING_RULE)[0].state == "resolved"
    state = _state(sessions)
    assert (state.failures, state.last_error) == (0, None)
    assert len(calls) == 2


def test_a_statement_timeout_is_a_failure(sessions: sessionmaker[Session]) -> None:
    _setting(sessions, "rules.statement_timeout_seconds", 1)

    def slow(session: Session, context: RuleContext) -> Iterable[Hit]:
        session.execute(text("SELECT pg_sleep(3)"))
        return ()

    sleepy = RuleDef(**{**RULE.__dict__, "evaluate": slow})
    _started(sessions)
    assert _run(sessions, NOW + timedelta(minutes=1), sleepy)["failed"] == ["R02"]
    assert _state(sessions).last_error == "OperationalError: QueryCanceled"


def test_a_rule_switched_off_starts_afresh(sessions: sessionmaker[Session]) -> None:
    _started(sessions)
    _setting(sessions, "rules.R02", {"enabled": False})
    _rows(sessions, HORIZON + timedelta(seconds=20), 5)
    assert _run(sessions, NOW + timedelta(minutes=1))["slices"] == 0
    assert _state(sessions).watermark is None
    _setting(sessions, "rules.R02", {})
    _started(sessions, NOW + timedelta(minutes=2))
    assert _run(sessions, NOW + timedelta(minutes=3))["opened"] == 0  # D12: no backfill


def test_an_old_backlog_is_skipped_and_recorded(sessions: sessionmaker[Session]) -> None:
    _started(sessions)
    _setting(sessions, "rules.max_catch_up_hours", 1)
    _setting(sessions, "rules.max_slice_seconds", 3600)
    later = NOW + timedelta(hours=3)
    details = _run(sessions, later)
    assert details["skipped_backlog"] == ["R02"]
    state = _state(sessions)
    assert state.skipped_until == later - timedelta(seconds=120) - timedelta(hours=1)
    assert state.watermark == later - timedelta(seconds=120)


def test_the_newest_input_row_is_recorded(sessions: sessionmaker[Session]) -> None:
    _started(sessions)
    assert _state(sessions).last_input_at is None
    _rows(sessions, HORIZON + timedelta(seconds=20), 1)
    _run(sessions, NOW + timedelta(minutes=1))
    assert _state(sessions).last_input_at == HORIZON + timedelta(seconds=20)
    assert _state(sessions).last_run_at == NOW + timedelta(minutes=1)
    assert rule_engine.data_status(None, NOW, timedelta(hours=24)) == "never"
    assert rule_engine.data_status(NOW, NOW, timedelta(hours=24)) == "ok"
    old = NOW - timedelta(hours=25)
    assert rule_engine.data_status(old, NOW, timedelta(hours=24)) == "no_data"


def test_a_backlog_puts_every_moment_into_a_window(sessions: sessionmaker[Session]) -> None:
    """Deviation W6-A-5: a slice is at most the rule's window long, so a burst early in a long
    backlog is still in the window of some slice end (a 600-s slice and a 300-s window would see
    only its last five minutes)."""
    _started(sessions)
    _setting(sessions, "rules.max_slice_seconds", 600)
    _rows(sessions, HORIZON + timedelta(seconds=30), 5)
    details = _run(sessions, NOW + timedelta(minutes=10))
    assert details["slices"] == 2  # 600 s of backlog in 300-s slices
    assert details["opened"] == 1


# -- T-A3: dedup, cooldown, evidence cap, condition ---------------------------------------------


def test_an_active_alert_is_updated_and_its_evidence_capped(
    sessions: sessionmaker[Session],
) -> None:
    _setting(sessions, "rules.evidence_max", 7)
    _started(sessions)
    _rows(sessions, HORIZON + timedelta(seconds=10), 5)
    first = NOW + timedelta(minutes=1)
    _run(sessions, first)
    _rows(sessions, first - timedelta(seconds=70), 4)
    details = _run(sessions, first + timedelta(minutes=1))
    assert (details["opened"], details["updated"]) == (0, 1)
    (alert,) = _alerts(sessions)
    assert alert.details["count"] == 5 + 9  # the window of the second slice holds all nine
    assert alert.details["evidence_total"] == 14
    evidence = _evidence(sessions, alert.id)
    assert [row.n for row in evidence] == list(range(1, 8))
    assert len({(row.tbl, row.row_id) for row in evidence}) == 7


def test_the_cooldown_counts_on_the_closed_alert(sessions: sessionmaker[Session]) -> None:
    _started(sessions)
    _rows(sessions, HORIZON + timedelta(seconds=10), 5)
    first = NOW + timedelta(minutes=1)
    _run(sessions, first)
    (alert,) = _alerts(sessions)
    with sessions() as session:
        loaded = session.get(Alert, alert.id)
        assert loaded is not None
        change_state(
            session, loaded, "resolved", by="guvenlik", user_id=None, now=first, audit=_no_audit
        )
        session.commit()
    second = first + timedelta(minutes=10)
    _rows(sessions, second - timedelta(seconds=130), 3)
    details = _run(sessions, second)
    assert (details["opened"], details["cooled"]) == (0, 1)
    (closed,) = _alerts(sessions)
    assert closed.details["after_close"]["count"] == 3
    # After the cooldown (1 h after the closed alert's time) a new alert opens.
    third = alert.time + timedelta(hours=1, minutes=5)
    _rows(sessions, third - timedelta(seconds=130), 3)
    _setting(sessions, "rules.max_catch_up_hours", 24)
    details = _run(sessions, third)
    assert details["opened"] == 1
    assert [item.state for item in _alerts(sessions)] == ["resolved", "open"]


def test_a_condition_rule_resolves_its_alert(sessions: sessionmaker[Session]) -> None:
    def run(at: datetime) -> dict[str, Any]:
        return _run(sessions, at, CONDITION)

    assert run(NOW)["started"] == ["R17"]
    _rows(sessions, HORIZON + timedelta(seconds=10), 5)
    run(NOW + timedelta(minutes=1))
    (alert,) = _alerts(sessions, "R17")
    assert alert.title == f"Güvenlik duvarı sağlığı: {SCANNER} (eşik aşıldı)"
    assert alert.details["rule_kind"] == "condition"
    run(NOW + timedelta(minutes=5))  # within clear_seconds: still open
    assert _alerts(sessions, "R17")[0].state == "open"
    details = run(NOW + timedelta(minutes=13))
    assert details["resolved"] == 1
    (alert,) = _alerts(sessions, "R17")
    assert alert.state == "resolved"
    assert alert.details["auto_resolved"]["reason"] == "condition_cleared"


def test_a_key_without_a_row_in_the_slice_raises_nothing(sessions: sessionmaker[Session]) -> None:
    """A group key needs a row in the slice: a window full of old rows is not a new hit."""
    _started(sessions)
    _rows(sessions, HORIZON + timedelta(seconds=10), 5)
    _run(sessions, NOW + timedelta(minutes=1))
    with sessions() as session:
        alert = active_alert(session, f"R02:{SCANNER}")
        assert alert is not None
    details = _run(sessions, NOW + timedelta(minutes=2))
    assert (details["slices"], details["hits"]) == (1, 0)


# -- T-A7: bound parameters ---------------------------------------------------------------------

STRANGE = ["deny'; DELETE FROM alerts; --", "100%", 'de"ny', "%", "d_ny"]


def test_list_parameters_are_bound_not_spliced(sessions: sessionmaker[Session]) -> None:
    """Quotes, semicolons and ``%`` in a list parameter change nothing but the match: they match
    only rows whose value is exactly that text."""
    _setting(sessions, "rules.R02", {"params": {"deny_actions": STRANGE, "threshold": 2}})
    _started(sessions)
    _rows(sessions, HORIZON + timedelta(seconds=10), 5)  # action "deny": not listed
    _rows(sessions, HORIZON + timedelta(seconds=10), 2, srcip=OTHER, action="100%")
    _run(sessions, NOW + timedelta(minutes=1))
    (alert,) = _alerts(sessions)
    assert alert.dedup_key == f"R02:{OTHER}"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(TABLE)) == 7


def test_hit_values_are_kept_as_json() -> None:
    """``details.values`` keeps a rule's lists and objects as JSON, not as their text."""
    values = {
        "macs": ("00:00:5e:00:53:e1", "00:00:5e:00:53:e2"),
        "ports": [22, 443],
        "share": 0.5,
        "night": date(2026, 11, 12),
        "at": NOW,
        "by": {"tcp": 3},
        "ip": ip_address("172.20.41.17"),
    }
    assert {name: rule_engine.json_value(value) for name, value in values.items()} == {
        "macs": ["00:00:5e:00:53:e1", "00:00:5e:00:53:e2"],
        "ports": [22, 443],
        "share": 0.5,
        "night": "2026-11-12",
        "at": "2026-11-12T09:45:00+00:00",
        "by": {"tcp": 3},
        "ip": "172.20.41.17",
    }


# -- T-A5: dry run --------------------------------------------------------------------------------


def _counts(sessions: sessionmaker[Session]) -> dict[str, int]:
    with sessions() as session:
        return {
            table: session.scalar(text(f"SELECT count(*) FROM {table}")) or 0  # noqa: S608 - fixed names
            for table in ("alerts", "alert_evidence", "alert_events", "rule_state", "settings")
        }


def test_the_dry_run_answers_what_the_live_run_does_and_writes_nothing(
    sessions: sessionmaker[Session],
) -> None:
    candidate = {"params": {"threshold": 4}}
    _setting(sessions, "rules.R02", candidate)
    _setting(sessions, "rules.max_slice_seconds", 3600)
    for hours, count, srcip in ((20, 5, SCANNER), (10, 3, SCANNER), (5, 6, OTHER), (2, 4, SCANNER)):
        _rows(sessions, HORIZON - timedelta(hours=hours, seconds=-10), count, srcip=srcip)
    before = _counts(sessions)
    with sessions() as session:
        result = dry_run(session, RULE, candidate, NOW).as_json()
    assert _counts(sessions) == before
    assert result["would_open"] == 2
    assert [(item["key"], item["count"]) for item in result["keys"]] == [(SCANNER, 9), (OTHER, 6)]
    assert result["truncated"] is False
    assert result["duration_ms"] >= 0
    # The live run over the same 24 hours opens the same alerts.
    with sessions() as session:
        session.add(RuleState(rule="R02", watermark=HORIZON - timedelta(hours=24), failures=0))
        session.commit()
    details = _run(sessions)
    assert details["opened"] == 2
    assert {alert.details["key"]: alert.details["count"] for alert in _alerts(sessions)} == {
        SCANNER: 9,
        OTHER: 6,
    }


def test_the_dry_run_refuses_invalid_candidates_and_times_out(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session, pytest.raises(RuleSettingsError) as caught:
        dry_run(session, RULE, {"params": {"threshold": 0}}, NOW)
    assert caught.value.field == "params.threshold"
    _setting(sessions, "rules.dry_run_timeout_seconds", 5)

    def slow(session: Session, context: RuleContext) -> Iterable[Hit]:
        session.execute(text("SELECT pg_sleep(6)"))
        return ()

    with sessions() as session, pytest.raises(DryRunTimeoutError):
        dry_run(session, RuleDef(**{**RULE.__dict__, "evaluate": slow}), {}, NOW)
    with sessions() as session, pytest.raises(KeyError):
        dry_run(session, "R15", {}, NOW)
