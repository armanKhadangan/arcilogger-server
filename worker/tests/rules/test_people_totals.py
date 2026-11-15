"""R13's day total and R14's night totals kept from slice to slice (#280): at every slice end
they give the alerts of one read of the period, with late logs, logs whose device clock runs
ahead, a late log that changes a counted log's increment, and a restart in the middle.

Every value is fictional (Örnek A.Ş., ``172.20.x``, RFC 5737 destinations).
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from functools import partial
from ipaddress import ip_network
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import site_day
from arcilogger_db.models import FgTraffic, RuleTrafficEvidence, RuleTrafficState
from arcilogger_worker.rules import people
from arcilogger_worker.rules.model import Actions, Hit, RuleContext, RuleDef, RuleSettings

ZONE = ZoneInfo("Europe/Istanbul")
DAY = date(2026, 11, 12)
#: Site midnight of DAY.
MIDNIGHT = datetime(2026, 11, 11, 21, 0, tzinfo=UTC)
SKEW = timedelta(minutes=5)
INTERNAL = (ip_network("172.16.0.0/12"),)
ADDRESSES = [f"172.20.43.{n}" for n in range(10, 16)]
KIB = 1024


@pytest.fixture
def rebuilds(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """The slice ends at which a period's totals were read whole."""
    ends: list[datetime] = []
    rebuild = people._TableStore.rebuild

    def counted(self: Any, session: Session, ctx: RuleContext, period: Any) -> None:
        ends.append(ctx.end)
        rebuild(self, session, ctx, period)

    monkeypatch.setattr(people._TableStore, "rebuild", counted)
    return ends


def forget(session: Session) -> None:
    """A restart that lost the totals (or a downgrade and upgrade): the next run reads whole."""
    session.execute(delete(RuleTrafficState))


@dataclass
class Log:
    srcip: str
    sessionid: int | None
    time: datetime
    received: datetime
    interim: bool
    sent: int
    rcvd: int
    sentdelta: int | None = None
    rcvddelta: int | None = None
    dstport: int = 443


def traffic(
    rng: random.Random, begin: datetime, end: datetime, every: tuple[int, int] = (60, 600)
) -> list[Log]:
    """Sessions of the addresses between ``begin`` and ``end``: interim logs every few minutes
    with their deltas, a final log with the counters; some logs come late, some devices' clocks
    run ahead, a few logs have no session id."""
    logs: list[Log] = []
    number = 0
    moment = begin
    while moment < end:
        moment += timedelta(seconds=rng.randint(*every))
        number += 1
        ip = rng.choice(ADDRESSES)
        sent = rcvd = 0
        at = moment
        for _ in range(rng.randint(0, 4)):  # interim logs
            at += timedelta(seconds=rng.randint(60, 900))
            step_sent, step_rcvd = rng.randint(1, 400) * KIB, rng.randint(1, 900) * KIB
            sent, rcvd = sent + step_sent, rcvd + step_rcvd
            logs.append(Log(ip, number, at, at, True, sent, rcvd, step_sent, step_rcvd))
        at += timedelta(seconds=rng.randint(30, 900))
        sent, rcvd = sent + rng.randint(0, 2000) * KIB, rcvd + rng.randint(0, 3000) * KIB
        logs.append(Log(ip, number, at, at, False, sent, rcvd))
    for log in logs:
        roll = rng.random()
        if roll < 0.15:  # committed late: received up to 40 minutes after its device time
            log.received = log.time + timedelta(seconds=rng.randint(60, 2400))
        elif roll < 0.2:  # the device's clock runs ahead (within the skew)
            log.time = log.received + timedelta(seconds=rng.randint(1, 240))
        elif roll < 0.22:
            log.sessionid = None
    # The counter case: a late interim log between an interim and the final log, with the
    # final log's counters (the final log then counts nothing more).
    final = next(log for log in logs if not log.interim and log.sessionid is not None)
    late = Log(
        final.srcip,
        final.sessionid,
        final.time - timedelta(seconds=5),
        final.time + timedelta(minutes=20),
        True,
        final.sent,
        final.rcvd,
    )
    return [*logs, late]


def write(session: Session, logs: list[Log]) -> None:
    session.execute(
        insert(FgTraffic),
        [
            {
                "time": log.time,
                "time_received": log.received,
                "source": "fortigate",
                "sender": "172.20.0.1",
                "raw_day": site_day(log.received),
                "raw_line": index + 1,
                "raw_offset": index * 1000,
                "subtype": "forward",
                "action": "close" if not log.interim else "accept",
                "srcip": log.srcip,
                "sessionid": log.sessionid,
                "dstip": "203.0.113.10",
                "dstport": log.dstport,
                "is_interim": log.interim,
                "sentbyte": log.sent,
                "rcvdbyte": log.rcvd,
                "sentdelta": log.sentdelta,
                "rcvddelta": log.rcvddelta,
                "extra": {},
            }
            for index, log in enumerate(logs)
        ],
    )
    session.commit()


def rule(rule_id: str) -> RuleDef:
    return next(entry for entry in people.RULES if entry.id == rule_id)


def settings(entry: RuleDef, params: dict[str, Any]) -> RuleSettings:
    values: dict[str, Any] = {param.key: param.default for param in entry.params}
    values.update(params)
    return RuleSettings(
        True,
        entry.severity,
        entry.window_seconds,
        entry.cooldown_seconds,
        Actions(email=True, webhook=True),
        values,
    )


def hits(
    session: Session, entry: RuleDef, chosen: RuleSettings, start: datetime, end: datetime
) -> list[Any]:
    ctx = RuleContext(chosen, start, end, SKEW, INTERNAL, ZONE)
    return sorted(normalized(hit) for hit in entry.evaluate(session, ctx))


def normalized(hit: Hit) -> tuple[Any, ...]:
    return (
        hit.key,
        hit.time,
        hit.count,
        tuple(sorted(hit.values.items())),
        tuple((ref.table, ref.row_id) for ref in hit.evidence),
        hit.person_id,
    )


def once(session: Session, run: Callable[[], list[Any]]) -> list[Any]:
    """``run`` as one read of the period: in a savepoint without the kept totals, rolled back."""
    nested = session.begin_nested()
    try:
        forget(session)
        return run()
    finally:
        nested.rollback()


def slice_by_slice(
    session: Session,
    entry: RuleDef,
    chosen: RuleSettings,
    begin: datetime,
    end: datetime,
    size: timedelta,
    restart_at: int,
    rebuilds: list[datetime],
) -> tuple[int, int, int]:
    """Every slice compared with one read; the totals forgotten at slice ``restart_at``.
    Returns the slices, the hits seen and how often the slices read a period whole."""
    start, index, seen, whole = begin, 0, 0, 0
    while start < end:
        stop = min(start + size, end)
        if index == restart_at:
            forget(session)
        before = len(rebuilds)
        incremental = hits(session, entry, chosen, start, stop)
        whole += len(rebuilds) - before
        reference = once(session, partial(hits, session, entry, chosen, start, stop))
        assert incremental == reference, f"slice {index} ({start:%H:%M}-{stop:%H:%M})"
        seen += len(incremental)
        start, index = stop, index + 1
    return index, seen, whole


def test_r13_day_total_slice_by_slice(db_session: Session, rebuilds: list[datetime]) -> None:
    rng = random.Random(5651)  # noqa: S311 - synthetic traffic
    # Dense enough that addresses reach the evidence limit (their lists are then kept).
    logs = traffic(rng, MIDNIGHT - timedelta(hours=1), MIDNIGHT + timedelta(hours=9), (10, 60))
    write(db_session, logs)
    entry = rule("R13")
    # A small day limit, so the day total raises alerts during the morning; the hour limit
    # stays out of the way.
    chosen = settings(entry, {"hour_bytes": 1024**4, "day_bytes": 20 * 1024 * KIB})
    slices, seen, whole = slice_by_slice(
        db_session,
        entry,
        chosen,
        MIDNIGHT - timedelta(minutes=30),
        MIDNIGHT + timedelta(hours=8),
        timedelta(minutes=10),
        restart_at=20,
        rebuilds=rebuilds,
    )
    assert slices > 40
    assert seen > 0, "the day total raised no alert: the comparison proves nothing"
    # Read whole only at the first slice, at midnight (a new day) and after the restart.
    assert whole == 3
    full = db_session.scalars(
        select(func.count())
        .select_from(RuleTrafficEvidence)
        .where(func.jsonb_array_length(RuleTrafficEvidence.refs) == people.EVIDENCE_LIMIT)
    ).one()
    assert full > 0, "no address's evidence list was kept full"


def test_r14_night_totals_slice_by_slice(db_session: Session, rebuilds: list[datetime]) -> None:
    rng = random.Random(1907)  # noqa: S311 - synthetic traffic
    write(db_session, traffic(rng, MIDNIGHT - timedelta(hours=3), MIDNIGHT + timedelta(hours=7)))
    entry = rule("R14")
    chosen = settings(
        entry,
        {
            "user_networks": (ip_network("172.20.43.0/24"),),
            "night_start": 22 * 3600,
            "night_end": 5 * 3600,
            "min_bytes": 10 * 1024 * KIB,
        },
    )
    slices, seen, whole = slice_by_slice(
        db_session,
        entry,
        chosen,
        MIDNIGHT - timedelta(hours=2),
        MIDNIGHT + timedelta(hours=6),
        timedelta(minutes=10),
        restart_at=25,
        rebuilds=rebuilds,
    )
    assert slices > 40
    assert seen > 0
    # Read whole only at the first slice and after the restart.
    assert whole == 2


def test_a_changed_setting_rebuilds_the_totals(db_session: Session) -> None:
    rng = random.Random(42)  # noqa: S311 - synthetic traffic
    write(db_session, traffic(rng, MIDNIGHT, MIDNIGHT + timedelta(hours=2)))
    entry = rule("R13")
    first = settings(entry, {"hour_bytes": 1024**4, "day_bytes": 5 * 1024 * KIB})
    start = MIDNIGHT + timedelta(minutes=50)
    hits(db_session, entry, first, start - timedelta(minutes=10), start)
    # Other internal networks change the amounts: the totals are not reused.
    ctx = RuleContext(
        first, start, start + timedelta(minutes=10), SKEW, (ip_network("172.20.43.0/26"),), ZONE
    )
    narrowed = sorted(normalized(hit) for hit in entry.evaluate(db_session, ctx))
    reference = once(
        db_session, lambda: sorted(normalized(hit) for hit in entry.evaluate(db_session, ctx))
    )
    assert narrowed == reference


def test_a_late_log_that_zeroes_a_counted_log(db_session: Session) -> None:
    """The counter case, pinned (deviation W6-E-1): a late interim log with the final log's
    counters arrives between an interim and the final log, so the final log counts nothing any
    more. The bytes and the logs drop by what the final log had counted; the hit's time is the
    newest counted log in the slice, newer than any log a late log can zero, so it stays exact
    too."""
    ip = "172.20.43.20"
    at = MIDNIGHT + timedelta(minutes=10)
    logs = [
        Log(
            ip,
            7001,
            at + timedelta(minutes=2),
            at + timedelta(minutes=2),
            True,
            40 * KIB,
            0,
            40 * KIB,
            0,
        ),
        Log(ip, 7001, at + timedelta(minutes=5), at + timedelta(minutes=5), False, 100 * KIB, 0),
        # The late interim log (device time before the final log, received ten minutes later).
        Log(
            ip,
            7001,
            at + timedelta(minutes=4),
            at + timedelta(minutes=15),
            True,
            100 * KIB,
            0,
            0,
            0,
        ),
        Log(
            ip,
            7002,
            at + timedelta(minutes=16),
            at + timedelta(minutes=16),
            False,
            5 * 1024 * KIB,
            0,
        ),
    ]
    write(db_session, logs)
    entry = rule("R13")
    chosen = settings(entry, {"hour_bytes": 1024**4, "day_bytes": KIB})
    first = hits(db_session, entry, chosen, at, at + timedelta(minutes=10))
    assert [dict(hit[3])["day_bytes"] for hit in first] == [100 * KIB]
    second = hits(db_session, entry, chosen, at + timedelta(minutes=10), at + timedelta(minutes=20))
    reference = once(
        db_session,
        partial(
            hits, db_session, entry, chosen, at + timedelta(minutes=10), at + timedelta(minutes=20)
        ),
    )
    assert second == reference
    (hit,) = second
    assert dict(hit[3])["day_bytes"] == 40 * KIB + 5 * 1024 * KIB  # the final log counts 0 now
    assert hit[2] == 2  # the interim log and the new session's log
    assert hit[1] == logs[3].received


def test_a_dry_run_keeps_the_same_totals_in_its_session(db_session: Session) -> None:
    """A read-only transaction (the dry run) keeps the totals in its session, not in the table,
    and gets every slice's alerts of the live runs."""
    rng = random.Random(77)  # noqa: S311 - synthetic traffic
    write(db_session, traffic(rng, MIDNIGHT, MIDNIGHT + timedelta(hours=3), (10, 60)))
    entry = rule("R13")
    chosen = settings(entry, {"hour_bytes": 1024**4, "day_bytes": 10 * 1024 * KIB})
    ends = [MIDNIGHT + timedelta(minutes=10 * n) for n in range(1, 19)]
    live = [hits(db_session, entry, chosen, end - timedelta(minutes=10), end) for end in ends]
    db_session.commit()
    db_session.execute(text("SET TRANSACTION READ ONLY"))
    dry = [hits(db_session, entry, chosen, end - timedelta(minutes=10), end) for end in ends]
    assert db_session.info.get("rules.period_totals") is not None
    db_session.rollback()
    assert dry == live
    assert any(live)


def test_a_kept_evidence_list_is_read_again_after_a_late_log(db_session: Session) -> None:
    """An address's kept list of its oldest counted logs is dropped when a late log makes one of
    them count 0, and read again: the next hit's evidence leaves that log out."""
    ip = "172.20.43.30"
    at = MIDNIGHT + timedelta(minutes=5)
    logs = [
        Log(ip, 8000, at, at, True, 40 * KIB, 0, 40 * KIB, 0),
        Log(ip, 8000, at + timedelta(seconds=30), at + timedelta(seconds=30), False, 100 * KIB, 0),
    ]
    logs += [
        Log(
            ip,
            8001 + n,
            at + timedelta(seconds=40 + n),
            at + timedelta(seconds=40 + n),
            False,
            10 * KIB,
            0,
        )
        for n in range(120)
    ]
    write(db_session, logs)
    entry = rule("R13")
    chosen = settings(entry, {"hour_bytes": 1024**4, "day_bytes": KIB})
    first = hits(db_session, entry, chosen, at - timedelta(minutes=1), at + timedelta(minutes=9))
    zeroed = db_session.scalars(
        select(FgTraffic.id).where(FgTraffic.sessionid == 8000, FgTraffic.is_interim.is_(False))
    ).one()
    assert zeroed in [row_id for _, row_id in first[0][4]]
    assert db_session.scalars(select(func.count()).select_from(RuleTrafficEvidence)).one() == 1
    # The late interim log with the final log's counters, and a new log of the address.
    late_at = at + timedelta(minutes=12)
    write(
        db_session,
        [
            Log(ip, 8000, at + timedelta(seconds=20), late_at, True, 100 * KIB, 0, 0, 0),
            Log(ip, 9000, late_at, late_at, False, 10 * KIB, 0),
        ],
    )
    start, end = at + timedelta(minutes=9), at + timedelta(minutes=19)
    second = hits(db_session, entry, chosen, start, end)
    assert second == once(db_session, partial(hits, db_session, entry, chosen, start, end))
    assert zeroed not in [row_id for _, row_id in second[0][4]]


def test_the_hour_is_the_days_increments_received_in_it(db_session: Session) -> None:
    """R13's hour (deviation W6-E-2): the bytes of the logs received in the window, each as its
    period counts it, summed from the kept minutes and the window's partial first minute; across
    midnight, yesterday's logs as yesterday counts them."""
    rng = random.Random(2026)  # noqa: S311 - synthetic traffic
    write(db_session, traffic(rng, MIDNIGHT - timedelta(hours=2), MIDNIGHT + timedelta(hours=2)))
    entry = rule("R13")
    chosen = settings(entry, {"hour_bytes": 1024**4, "day_bytes": 1024**4})
    for minutes in range(-20, 100, 7):
        end = MIDNIGHT + timedelta(minutes=minutes, seconds=17)
        ctx = RuleContext(chosen, end - timedelta(minutes=7), end, SKEW, INTERNAL, ZONE)
        hits(db_session, entry, chosen, ctx.start, ctx.end)  # counts the periods on to the end
        today = people._period(
            db_session, ctx, "R13", people._site_day_start(ctx), None, True, list(INTERNAL)
        )
        window = people._window_buckets(db_session, ctx, today, ctx.window_start, ADDRESSES)
        kept: dict[str, int] = defaultdict(int)
        for bucket in window:
            kept[bucket.srcip] += bucket.amount
        expected: dict[str, int] = defaultdict(int)
        for period, until in people._window_periods(db_session, ctx, today, ctx.window_start):
            capped = replace(ctx, end=min(ctx.end, until or ctx.end))
            rows = people._traffic_rows(
                db_session,
                capped,
                period.since,
                sent_only=True,
                networks=list(INTERNAL),
                scope="all",
                period=True,
            )
            for ip, amount in db_session.execute(
                select(rows.c.srcip, func.sum(rows.c.amount))
                .where(rows.c.wanted, rows.c.time_received > ctx.window_start)
                .group_by(rows.c.srcip)
            ):
                expected[str(ip)] += int(amount)
        assert dict(kept) == dict(expected), f"window ending {end:%H:%M:%S}"
