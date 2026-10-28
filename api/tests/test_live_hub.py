"""The live tail hub without HTTP (contract 2.14.3, 2.14.5): streams opened directly on a hub
with short intervals, a temporary spool and a clock the test moves.

Parse budget (``parse_skipped`` for streams on the folder and sender only, ``parse_budget``
drops for the others), the byte pre-filter, rate and queue drops counted exactly, a file far
ahead skipped with its lines counted (``behind``), the midnight hand-over, the sealer deleting
yesterday's file, a replaced file (``gap``), network members and new unknown senders joining,
``rawlog.invalid`` lines, time limit, slow clients and lingering out with ``live.close``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import time
from collections.abc import Callable, Coroutine
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.live import events
from arcilogger_api.live import hub as hub_module
from arcilogger_api.live.events import Entry
from arcilogger_api.live.hub import Connection, Hub, LiveSettings, Stream, Timing
from arcilogger_api.live.testing import spool_line
from arcilogger_api.search import SiteNetwork, compile_matcher, parse_query, windows
from arcilogger_api.testing import EXAMPLE_SITE_NAME
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, site_day
from arcilogger_db.models import AuditLog

# A stream lingers from the moment it is created until it is attached, and the cycle that opens
# it may seed hundreds of lines (0.13 s with coverage on Linux, several times that on a slow
# Windows runner): a 0.5 s linger on the real clock ran out before the test attached. Lingering
# out is tested in test_time_limit_and_lingering_out_are_audited, on the clock the test moves.
FAST = Timing(poll=0.02, listing=0.05, idle_stop=60.0, linger=60.0, slow_client=0.5)
FIREWALL = "172.31.255.1"


def traffic(srcip: str = "172.20.41.105", action: str = "accept", tag: str = "") -> str:
    return (
        '<189>date=2026-10-26 time=10:00:00 devname="FIREWALL-01" devid="FGT0000000000000" '
        'logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" '
        f'srcip={srcip} srcport=51544 srcintf="port2" dstip=203.0.113.10 dstport=443 '
        f'dstintf="wan1" policyid=12 sessionid=3100101 proto=6 action="{action}" '
        f'service="HTTPS" msg="{tag}"'
    )


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "site_name": EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": FIREWALL},
                {"name": "ap", "type": "mikrotik", "address": "172.20.10.192/26"},
            ],
        }
    )


class Clock:
    """Wall time stands still until the test moves it; monotonic time runs on (plus what the
    test adds)."""

    def __init__(self, at: datetime) -> None:
        self.at = at
        self.extra = 0.0

    def now(self) -> datetime:
        # Not on real time: the midnight tests open their stream at 23:59:59 and a first read
        # cycle that took a second on a slow runner already followed the next day's file.
        return self.at

    def monotonic(self) -> float:
        return time.monotonic() + self.extra


@pytest.fixture
def clock() -> Clock:
    # A fixed mid-day site moment, never the real now: a test that writes a line and then moves
    # the clock (or reads site_day a moment later) must not cross a real site midnight.
    return Clock(datetime(2026, 10, 26, 12, 0, tzinfo=SITE_TZ))


@pytest.fixture
def hub(app: FastAPI, clock: Clock) -> Hub:
    return Hub(app.state.arci, timing=FAST, clock=clock.monotonic, now=clock.now)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class Spool:
    def __init__(self, layout: Layout, clock: Clock) -> None:
        self.layout, self.clock = layout, clock

    def path(self, folder: str, day: date | None = None) -> Path:
        return self.layout.spool_file(folder, day or site_day(self.clock.now()))

    def write(
        self, folder: str, *messages: str | bytes, day: date | None = None, raw: bool = False
    ) -> Path:
        path = self.path(folder, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for message in messages:
                data = message if isinstance(message, bytes) else message.encode()
                handle.write(data + b"\n" if raw else spool_line(data, FIREWALL))
        return path


@pytest.fixture
def spool(layout: Layout, clock: Clock) -> Spool:
    return Spool(layout, clock)


def stream(
    hub: Hub,
    q: str = "",
    *,
    settings: LiveSettings | None = None,
    user_id: int = 1,
    backfill: int = 0,
) -> Stream:
    chosen = settings or LiveSettings()
    return Stream(
        id=secrets.token_urlsafe(8),
        user_id=user_id,
        username=f"kullanici{user_id}",
        token_hash="0" * 64,
        ip="172.20.10.37",
        query=q,
        matcher=compile_matcher(parse_query(q), SiteNetwork(network_sources=frozenset({"ap"}))),
        backfill=backfill,
        settings=chosen,
        notice_version="1",
        opened_at=hub.now(),
        opened_clock=hub.clock(),
        clock=hub.clock,
    )


async def connect(hub: Hub, opened: Stream, settings: LiveSettings | None = None) -> Connection:
    await hub.open(opened, settings or opened.settings, SiteNetwork())
    connection = Connection()
    hub.attach(opened, connection, None)
    return connection


def drain(connection: Connection) -> list[Entry]:
    found: list[Entry] = []
    while not connection.queue.empty():
        item = connection.queue.get_nowait()
        if isinstance(item, Entry):
            found.append(item)
    return found


async def until(check: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        await asyncio.sleep(0.01)
    assert check()


def test_parse_budget_skips_lines_honestly(hub: Hub, spool: Spool) -> None:
    """Beyond the hub's parse budget a line still reaches a stream that filters on the folder
    only (``parse_skipped``), and is counted as ``parse_budget`` by one that needs fields."""
    settings = LiveSettings(parse_lines_per_second=50, max_lines_per_second=500)

    async def scenario() -> None:
        everything, typed = (
            stream(hub, settings=settings),
            stream(hub, "srcip:172.20.41.105", settings=settings),
        )
        first = await connect(hub, everything)
        second = await connect(hub, typed)
        spool.write("fortigate", *(traffic(tag=f"n{n}") for n in range(300)))
        await until(lambda: hub.seq >= 300)
        await asyncio.sleep(0.05)
        all_lines, typed_lines = drain(first), drain(second)
        assert len(all_lines) == 300
        parsed = [entry for entry in all_lines if entry.state == "parsed"]
        assert 50 <= len(parsed) < 80  # the budget refills while the lines are read
        assert all(entry.state == "skipped" for entry in all_lines if entry not in parsed)
        assert [entry.seq for entry in typed_lines] == [entry.seq for entry in parsed]
        assert typed.dropped == {"parse_budget": 300 - len(parsed)}
        assert everything.dropped == {}
        assert hub.skipped_total == 300 - len(parsed)
        await hub.stop()

    run(scenario())


def test_lines_no_stream_can_match_are_not_parsed(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        opened = stream(hub, '"yasakli-site"')
        connection = await connect(hub, opened)
        lines = [traffic(tag="sıradan") for _ in range(50)]
        lines[17] = traffic(tag="YASAKLI-SITE erişimi")
        spool.write("fortigate", *lines)
        await until(lambda: hub.seq >= 50)
        await asyncio.sleep(0.05)
        found = drain(connection)
        assert [entry.line for entry in found] == [18]
        states = [entry.state for entry in hub.buffer]
        assert states.count("parsed") == 1  # the others were not even parsed
        assert hub.skipped_total == 0  # and not counted against the budget
        assert opened.dropped == {}
        await hub.stop()

    run(scenario())


def test_rate_limit_and_slow_client_are_counted_exactly(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        slow = LiveSettings(max_lines_per_second=10)
        limited = stream(hub, settings=slow)
        connection = await connect(hub, limited)
        spool.write("fortigate", *(traffic() for _ in range(100)))
        await until(lambda: hub.seq >= 100)
        await asyncio.sleep(0.05)
        assert len(drain(connection)) + limited.dropped["rate_limited"] == 100
        assert limited.dropped["rate_limited"] >= 85
        # a client that reads nothing: the queue fills, then the stream ends
        fast = LiveSettings(max_lines_per_second=500)
        stuck = stream(hub, settings=fast)
        held = await connect(hub, stuck)
        spool.write("fortigate", *(traffic() for _ in range(500)))
        await until(lambda: held.queue.full())
        await asyncio.sleep(1.1)  # the bucket refills
        spool.write("fortigate", *(traffic() for _ in range(40)))
        await until(lambda: stuck.dropped.get("slow_client", 0) == 40)
        assert stuck.dropped.get("rate_limited", 0) == 0
        await until(lambda: held.ending)  # full for longer than slow_client seconds
        assert b"event: end" in held.controls[-1]
        assert b'"code":"slow_client"' in held.controls[-1]
        assert stuck.closed == "slow_client"
        await hub.stop()

    run(scenario())


def test_a_file_far_ahead_is_skipped_and_counted(
    hub: Hub, spool: Spool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_module, "BEHIND_BYTES", 10_000)

    async def scenario() -> None:
        opened = stream(hub)
        connection = await connect(hub, opened)
        hub.timing = replace(FAST, poll=0.5)  # the whole burst is written before the next poll
        await asyncio.sleep(0.6)
        spool.write("fortigate", *(traffic() for _ in range(200)))
        await until(lambda: opened.dropped.get("behind", 0) > 0, seconds=3)
        assert opened.dropped == {"behind": 200}
        assert drain(connection) == []
        spool.write("fortigate", traffic(srcip="172.20.41.7"))
        await until(lambda: not connection.queue.empty(), seconds=3)
        assert [entry.line for entry in drain(connection)] == [201]
        await hub.stop()

    run(scenario())


def test_midnight_hand_over_and_the_sealer_deleting_yesterday(
    hub: Hub, spool: Spool, clock: Clock
) -> None:
    clock.at = datetime(2026, 10, 25, 23, 59, 59, tzinfo=SITE_TZ)
    yesterday, today = date(2026, 10, 25), date(2026, 10, 26)

    async def scenario() -> None:
        opened = stream(hub)
        connection = await connect(hub, opened)
        spool.write("fortigate", traffic(tag="once"), day=yesterday)
        await until(lambda: not connection.queue.empty())
        clock.at += timedelta(seconds=2)  # 00:00:01 of the new day
        spool.write("fortigate", traffic(tag="geç"), day=yesterday)  # still written after midnight
        spool.write("fortigate", traffic(tag="yeni"), day=today)
        await until(lambda: hub.seq >= 3)
        await asyncio.sleep(0.05)
        found = [(entry.day, entry.line) for entry in drain(connection)]
        assert found == [(yesterday, 1), (yesterday, 2), (today, 1)]
        spool.path("fortigate", yesterday).unlink()  # sealed and deleted
        spool.write("fortigate", traffic(tag="sonra"), day=today)
        await until(lambda: not connection.queue.empty())
        assert [(entry.day, entry.line) for entry in drain(connection)] == [(today, 2)]
        assert opened.closed is None
        await hub.stop()

    run(scenario())


def test_a_stream_opened_in_the_first_cycle_after_midnight_starts_at_line_0(
    hub: Hub, spool: Spool, clock: Clock
) -> None:
    clock.at = datetime(2026, 10, 25, 23, 59, 59, tzinfo=SITE_TZ)
    yesterday, today = date(2026, 10, 25), date(2026, 10, 26)

    async def scenario() -> None:
        await connect(hub, stream(hub))
        spool.write("fortigate", *(traffic(tag=f"dün {n}") for n in range(3)), day=yesterday)
        await until(lambda: hub.seq >= 3)
        clock.at += timedelta(seconds=2)  # no read cycle between midnight and the new stream
        later = stream(hub)
        folders = await hub.open(later, later.settings, SiteNetwork())
        # Yesterday's 3 lines are not "already there" in today's file.
        assert folders == [{"folder": "fortigate", "day": today, "start_line": 0}]
        await hub.stop()

    run(scenario())


def test_a_replaced_file_is_announced_and_followed_from_its_end(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        opened = stream(hub)
        connection = await connect(hub, opened)
        path = spool.write("fortigate", traffic(), traffic())
        await until(lambda: hub.seq >= 2)
        drain(connection)
        replacement = path.with_name("yeni.tmp")
        replacement.write_bytes(spool_line(traffic(tag="baska"), FIREWALL) * 3)
        replacement.replace(path)
        await until(lambda: bool(connection.controls))
        assert b'"reason":"file_replaced"' in connection.controls[0]
        drain(connection)  # the wake-up of the control frame
        spool.write("fortigate", traffic(tag="devam"))
        await until(lambda: not connection.queue.empty())
        assert [entry.line for entry in drain(connection)] == [4]
        await hub.stop()

    run(scenario())


def test_network_members_and_new_unknown_senders_join_at_once(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        members, unknown = stream(hub, "source:ap"), stream(hub, "source:unknown-*")
        first = await connect(hub, members)
        second = await connect(hub, unknown)
        spool.write(
            "ap-172.20.10.200", b"<30>2026-10-26T10:00:00.000+03:00 AP-2 wireless,info deneme"
        )
        spool.write("unknown-198.51.100.23", b"<13>Oct 26 10:00:00 yeni-cihaz test mesaji")
        spool.write("fortigate", traffic())
        await until(lambda: not first.queue.empty() and not second.queue.empty())
        await asyncio.sleep(0.1)
        assert [entry.folder for entry in drain(first)] == ["ap-172.20.10.200"]
        assert [entry.folder for entry in drain(second)] == ["unknown-198.51.100.23"]
        await hub.stop()

    run(scenario())


def test_lines_not_in_spool_format_are_shown_unparsed(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        everything, typed = stream(hub), stream(hub, "type:traffic")
        first = await connect(hub, everything)
        second = await connect(hub, typed)
        spool.write("fortigate", b"\x00\x00 torn fragment", raw=True)
        spool.write("fortigate", traffic())
        await until(lambda: hub.seq >= 2)
        await asyncio.sleep(0.05)
        shown = drain(first)
        assert [(entry.state, entry.parse_error) for entry in shown] == [
            ("invalid", "rawlog.invalid"),
            ("parsed", None),
        ]
        assert [entry.line for entry in drain(second)] == [2]
        assert shown[0].frame(hub.epoch, hub.site).count(b'"parsed":false') == 1
        await hub.stop()

    run(scenario())


def test_backfill_comes_from_the_end_of_the_file(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        spool.write("fortigate", *(traffic(srcip=f"172.20.41.{n}") for n in range(1, 21)))
        opened = stream(hub, "srcip:172.20.41.0/24 -srcip:172.20.41.20", backfill=3)
        folders = await hub.open(opened, opened.settings, SiteNetwork())
        assert folders == [{"folder": "fortigate", "day": site_day(hub.now()), "start_line": 20}]
        items = hub.attach(opened, Connection(), None)
        assert [item.line for item in items if isinstance(item, Entry)] == [17, 18, 19]
        await hub.stop()

    run(scenario())


def test_a_folder_attached_again_is_never_buffered_twice(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        spool.write("fortigate", *(traffic(tag=f"satır {n}") for n in range(1, 6)))
        first = stream(hub, "source:fortigate", backfill=5)
        connection = await connect(hub, first)
        await hub.close(first, "closed")
        hub.detach(first, connection)
        await until(lambda: hub.status()["hub"]["files"] == 0)  # no stream wants the folder
        assert len(hub.buffer) == 5  # its lines stay until the hub stops
        second = stream(hub, "source:fortigate", backfill=10)
        await hub.open(second, second.settings, SiteNetwork())
        items = hub.attach(second, Connection(), None)
        assert [item.line for item in items if isinstance(item, Entry)] == [1, 2, 3, 4, 5]
        assert [entry.line for entry in hub.buffer] == [1, 2, 3, 4, 5]
        await hub.stop()

    run(scenario())


def test_the_backfill_shows_the_newest_lines_within_the_parse_budget(
    hub: Hub, spool: Spool
) -> None:
    """Seeding parses the file's end newest first, so a typed stream's backfill shows the newest
    matches; older lines the budget left unparsed are counted, never hidden (R9)."""
    settings = LiveSettings(parse_lines_per_second=100)

    async def scenario() -> None:
        spool.write("fortigate", *(traffic(tag=f"n{n}") for n in range(600)))
        typed = stream(hub, "type:traffic", settings=settings, backfill=50)
        await hub.open(typed, settings, SiteNetwork())
        items = hub.attach(typed, Connection(), None)
        shown = [item.line for item in items if isinstance(item, Entry)]
        assert shown == list(range(551, 601))
        assert typed.dropped == {}
        parsed = [entry.line for entry in hub.buffer if entry.state == "parsed"]
        assert parsed[-100:] == list(range(501, 601))  # the budget went to the newest
        assert len(parsed) < 300  # (and what refilled while the rest was read)
        wide = stream(hub, "type:traffic", settings=settings, backfill=600)
        await hub.open(wide, settings, SiteNetwork())
        items = hub.attach(wide, Connection(), None)
        shown = [item.line for item in items if isinstance(item, Entry)]
        assert shown == parsed
        assert wide.dropped == {"parse_budget": 600 - len(parsed)}
        other = stream(hub, "source:ap type:traffic", settings=settings, user_id=2, backfill=50)
        await hub.open(other, settings, SiteNetwork())
        assert hub.attach(other, Connection(), None) == []
        assert other.dropped == {}  # another folder's lines are not its own
        await hub.stop()

    run(scenario())


def test_a_resume_never_reaches_back_before_the_stream(hub: Hub, spool: Spool) -> None:
    """``Last-Event-ID: <epoch>:0`` replays nothing older than the stream's first line (its
    oldest backfill line, else the first one after it opened): the backfill bound holds and
    nothing that was never the stream's is counted."""

    async def scenario() -> None:
        spool.write("fortigate", *(traffic(tag=f"once{n}") for n in range(300)))
        none = stream(hub, backfill=0)
        await hub.open(none, none.settings, SiteNetwork())
        first = hub.attach(none, Connection(), (hub.epoch, 0))  # the very first connection
        assert first == []
        connection = none.connection
        assert connection is not None
        spool.write("fortigate", traffic(tag="sonra"))
        await until(lambda: not connection.queue.empty())
        [live] = drain(connection)
        hub.detach(none, connection)  # Duraklat
        again = hub.attach(none, Connection(), (hub.epoch, 0))
        assert again == [live]  # its own line again, no gap, nothing from before
        assert none.dropped == {}
        two = stream(hub, backfill=2)
        await hub.open(two, two.settings, SiteNetwork())
        shown = hub.attach(two, Connection(), None)
        assert [item.line for item in shown if isinstance(item, Entry)] == [300, 301]
        assert two.connection is not None
        hub.detach(two, two.connection)
        assert hub.attach(two, Connection(), (hub.epoch, 0)) == shown
        assert two.dropped == {}
        await hub.stop()

    run(scenario())


def test_time_limit_and_lingering_out_are_audited(
    hub: Hub, spool: Spool, clock: Clock, app_session_factory: sessionmaker[Session]
) -> None:
    async def scenario() -> None:
        limited = stream(hub, settings=LiveSettings(max_minutes=5))
        connection = await connect(hub, limited)
        spool.write("fortigate", traffic())
        await until(lambda: not connection.queue.empty())
        item = connection.queue.get_nowait()
        assert isinstance(item, Entry)
        limited.sent(item)
        clock.at += timedelta(minutes=6)
        await until(lambda: connection.ending)
        assert b'"code":"time_limit"' in connection.controls[-1]
        assert "5 dakika" in connection.controls[-1].decode()
        hub.detach(limited, connection)  # the writer sent the end and returned
        paused = stream(hub)
        held = await connect(hub, paused)
        hub.detach(paused, held)
        clock.extra += FAST.linger  # paused for the whole linger, without waiting for it
        await until(lambda: paused.closed == "lingered", seconds=3)
        await until(lambda: not hub._background)
        await hub.stop()

    run(scenario())
    with app_session_factory() as session:
        closes = list(
            session.scalars(
                select(AuditLog).where(AuditLog.action == "live.close").order_by(AuditLog.id)
            )
        )
    assert [entry.details["reason"] for entry in closes] == ["time_limit", "lingered"]
    first = closes[0].details
    assert first["delivered"] == 1
    assert first["ranges"] == [
        {"folder": "fortigate", "day": site_day(clock.at).isoformat(), "from_line": 1, "to_line": 1}
    ]
    assert set(first["dropped"]) == {"rate_limited", "slow_client", "parse_budget", "behind"}
    assert "srcip" not in str(first)  # counts and references only
    assert closes[0].actor_username == "kullanici1"


def test_the_hub_stops_after_the_last_stream(hub: Hub, spool: Spool) -> None:
    async def scenario() -> None:
        hub.timing = replace(FAST, idle_stop=0.2)
        opened = stream(hub)
        connection = await connect(hub, opened)
        assert hub.running
        assert hub.status()["hub"]["files"] == 0  # no file of today yet
        spool.write("fortigate", traffic())
        await until(lambda: hub.status()["hub"]["files"] == 1)
        await hub.close(opened, "closed")
        hub.detach(opened, connection)
        await until(lambda: not hub.running, seconds=3)
        assert hub.status()["streams"] == 0
        assert list(hub.buffer) == []

    run(scenario())


def test_settings_load_every_live_key(app_session_factory: sessionmaker[Session]) -> None:
    with app_session_factory() as session:
        assert LiveSettings.load(session) == LiveSettings()


def test_a_live_line_names_the_most_specific_vlan_as_arama_does() -> None:
    # The wider subnet first, as an unordered SELECT of the VLANs may give it; a search row of
    # the same line names VLAN 41 (``search.windows.vlan_of``), so the live line must too.
    site = SiteNetwork(
        vlans={
            90: ipaddress.ip_network("172.20.0.0/16"),
            41: ipaddress.ip_network("172.20.41.0/24"),
        }
    )
    row = {"srcip": "172.20.41.5", "hostname": "ornek.example", "action": "passthrough"}
    shown = events.summary("fg_web", row, site)
    assert shown == windows.summary("fg_web", row, site)
    assert shown["vlan_id"] == 41
    assert events.summary("fg_web", {**row, "srcip": "172.20.99.5"}, site)["vlan_id"] == 90
