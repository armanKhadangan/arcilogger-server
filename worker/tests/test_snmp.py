"""The internet line's speed over SNMPv3 (Wave 5 contract §6, W5-N tests of §6.5).

The poller is driven through a test double at the library boundary (:class:`FakeAgent`, the
:class:`~arcilogger_core.snmp.SnmpClient` protocol): rates, counter and ``sysUpTime``
resets, the ``ifName`` mapping and its re-walk, every status code, the state file and its bounds,
the job, the ``R20-snmp`` alert and that no key ever reaches a log line, the file or an alert.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore, snmp_auth_key, snmp_priv_key
from arcilogger_core.snmp import (
    IF_HC_IN_OCTETS,
    IF_HC_OUT_OCTETS,
    IF_HIGH_SPEED,
    IF_NAME,
    IF_OPER_STATUS,
    MISSING,
    SYS_UPTIME,
    SnmpError,
    Status,
    Target,
    Value,
)
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.models import Alert
from arcilogger_worker.alerts import texts
from arcilogger_worker.alerts.store import AUTO_RESOLVED
from arcilogger_worker.snmp import job as snmp_job
from arcilogger_worker.snmp.poller import poll_source
from arcilogger_worker.snmp.settings import SnmpSource, snmp_sources
from arcilogger_worker.snmp.state import (
    MINUTES_KEEP,
    SAMPLES_KEEP,
    SourceState,
    WanState,
    read_state,
    write_state,
)

T0 = datetime(2026, 10, 12, 10, 0, tzinfo=SITE_TZ)
AUTH_KEY = "auth-key-Örnek-1"
PRIV_KEY = "priv-key-Örnek-2"
TARGET = Target("172.20.0.1", 161, "arcilogger", "sha256", "aes128", AUTH_KEY, PRIV_KEY)
SOURCE = SnmpSource(
    name="fw-1",
    host="172.20.0.1",
    port=161,
    user="arcilogger",
    auth="sha256",
    priv="aes128",
    interfaces=("wan1", "wan2"),
    capacity={"wan1": (1000.0, 100.0)},
    interval_seconds=15,
)


@dataclass
class Interface:
    name: str
    octets_in: int = 0
    octets_out: int = 0
    oper: int = 1
    speed: int = 1000
    hc: bool = True


@dataclass
class FakeAgent:
    """An SNMP agent at the library boundary: ``ifIndex`` → interface, ``sysUpTime``, a failure."""

    interfaces: dict[int, Interface] = field(default_factory=dict)
    uptime: int = 100_000
    error: Status | None = None
    walks: int = 0
    gets: list[list[str]] = field(default_factory=list)

    async def walk(self, target: Target, oid: str) -> dict[str, str]:
        assert oid == IF_NAME
        self.walks += 1
        if self.error:
            raise SnmpError(self.error, "test")
        return {f"{IF_NAME}.{index}": i.name for index, i in self.interfaces.items()}

    async def get(self, target: Target, oids: Sequence[str]) -> dict[str, Value]:
        self.gets.append(list(oids))
        if self.error:
            raise SnmpError(self.error, "test")
        values: dict[str, Value] = {}
        for oid in oids:
            if oid == SYS_UPTIME:
                values[oid] = self.uptime
                continue
            column, index = oid.rsplit(".", 1)
            interface = self.interfaces.get(int(index))
            if interface is None:
                values[oid] = MISSING
            elif column == IF_HC_IN_OCTETS:
                values[oid] = interface.octets_in if interface.hc else MISSING
            elif column == IF_HC_OUT_OCTETS:
                values[oid] = interface.octets_out if interface.hc else MISSING
            elif column == IF_OPER_STATUS:
                values[oid] = interface.oper
            elif column == IF_HIGH_SPEED:
                values[oid] = interface.speed
        return values

    def tick(self, seconds: float, down: int = 0, up: int = 0, index: int = 1) -> None:
        """``seconds`` pass on the device; ``down``/``up`` bytes cross interface ``index``."""
        self.uptime += int(seconds * 100)
        self.interfaces[index].octets_in += down
        self.interfaces[index].octets_out += up


def agent() -> FakeAgent:
    return FakeAgent({1: Interface("wan1", 10_000, 20_000), 2: Interface("wan2", 5, 6)})


def poll(
    fake: FakeAgent, state: SourceState, now: datetime, target: Target | None = TARGET
) -> None:
    asyncio.run(poll_source(SOURCE, target, fake, state, now))


# -- rates, resets and the ifName mapping ---------------------------------------------------------


def test_a_rate_is_delta_octets_times_eight_over_delta_t() -> None:
    fake, state = agent(), SourceState("fw-1")
    poll(fake, state, T0)
    wan1 = state.lines["wan1"]
    assert (wan1.status, wan1.if_index, wan1.samples, wan1.updated_at) == ("ok", 1, [], T0)
    assert wan1.capacity == (1000.0, 100.0)
    fake.tick(15, down=1_875_000, up=187_500)  # 1 Mbit/s down, 0.1 Mbit/s up over 15 s
    poll(fake, state, T0 + timedelta(seconds=15))
    assert wan1.samples == [(T0 + timedelta(seconds=15), 1_000_000.0, 100_000.0)]
    assert (wan1.oper_status, wan1.high_speed_mbps) == ("up", 1000)
    assert state.lines["wan2"].samples == [(T0 + timedelta(seconds=15), 0.0, 0.0)]
    assert fake.walks == 1  # the names were walked once
    assert fake.gets[-1][0] == SYS_UPTIME
    assert len(fake.gets[-1]) == 1 + 2 * 4  # one GET: sysUpTime and four objects per line


def test_a_counter_reset_gives_no_rate_and_starts_the_next() -> None:
    fake, state = agent(), SourceState("fw-1")
    poll(fake, state, T0)
    fake.tick(15)
    fake.interfaces[1].octets_in = 3  # cleared on the device
    poll(fake, state, T0 + timedelta(seconds=15))
    assert state.lines["wan1"].samples == []
    assert state.lines["wan1"].status == "ok"
    fake.tick(15, down=15 * 1000 // 8)
    poll(fake, state, T0 + timedelta(seconds=30))
    assert [s[1] for s in state.lines["wan1"].samples] == [1000.0]


def test_sysuptime_going_back_drops_the_read_and_walks_again() -> None:
    fake, state = agent(), SourceState("fw-1")
    poll(fake, state, T0)
    fake.uptime = 500  # the device restarted; its interfaces were renumbered
    fake.interfaces = {7: Interface("wan1", 1, 1), 8: Interface("wan2", 1, 1)}
    poll(fake, state, T0 + timedelta(seconds=15))
    assert (state.lines["wan1"].samples, state.lines["wan1"].if_index) == ([], None)
    poll(fake, state, T0 + timedelta(seconds=30))  # walks again, starts the rates again
    assert fake.walks == 2
    assert state.lines["wan1"].if_index == 7
    assert state.lines["wan1"].samples == []
    fake.tick(15, down=15 * 8000 // 8, index=7)
    poll(fake, state, T0 + timedelta(seconds=45))
    assert [s[1] for s in state.lines["wan1"].samples] == [8000.0]


def test_a_name_that_vanished_is_walked_again() -> None:
    fake, state = agent(), SourceState("fw-1")
    poll(fake, state, T0)
    moved = fake.interfaces.pop(1)
    fake.interfaces[9] = moved  # same uptime, another index (a re-numbering agent)
    poll(fake, state, T0 + timedelta(seconds=15))
    wan1 = state.lines["wan1"]
    assert (wan1.status, wan1.if_index, wan1.last_error_at) == (
        "no_such_interface",
        None,
        T0 + timedelta(seconds=15),
    )
    assert state.lines["wan2"].status == "ok"
    poll(fake, state, T0 + timedelta(seconds=30))
    assert (wan1.status, wan1.if_index, fake.walks) == ("ok", 9, 2)


def test_an_unknown_interface_name_is_no_such_interface() -> None:
    fake, state = agent(), SourceState("fw-1")
    fake.interfaces.pop(2)
    poll(fake, state, T0)
    assert state.lines["wan2"].status == "no_such_interface"
    assert state.lines["wan1"].status == "ok"
    poll(fake, state, T0 + timedelta(seconds=15))
    assert fake.walks == 2  # a missing name is looked for again at each poll


def test_an_agent_without_64_bit_counters() -> None:
    fake, state = agent(), SourceState("fw-1")
    fake.interfaces[1].hc = False
    poll(fake, state, T0)
    wan1 = state.lines["wan1"]
    assert (wan1.status, wan1.oper_status, wan1.counters) == ("snmp_no_hc_counters", "up", None)


@pytest.mark.parametrize("status", ["timeout", "auth_failed"])
def test_a_failed_request_marks_every_line(status: Status) -> None:
    fake, state = agent(), SourceState("fw-1")
    poll(fake, state, T0)
    fake.error = status
    poll(fake, state, T0 + timedelta(seconds=15))
    assert {line.status for line in state.lines.values()} == {status}
    assert state.lines["wan1"].updated_at == T0  # the last good read stays
    fake.error = None
    fake.tick(30, down=30 * 2000 // 8)
    poll(fake, state, T0 + timedelta(seconds=30))  # the rate spans the failed poll
    assert [s[1] for s in state.lines["wan1"].samples] == [2000.0]


def test_a_missing_key_is_auth_failed_without_a_request() -> None:
    fake, state = agent(), SourceState("fw-1")
    poll(fake, state, T0, target=None)
    assert {line.status for line in state.lines.values()} == {"auth_failed"}
    assert (fake.walks, fake.gets) == (0, [])


def test_the_lines_follow_the_configured_interfaces() -> None:
    fake, state = agent(), SourceState("fw-1")
    poll(fake, state, T0)
    one = SnmpSource(**{**SOURCE.__dict__, "interfaces": ("wan2",), "capacity": {}})
    asyncio.run(poll_source(one, TARGET, fake, state, T0 + timedelta(seconds=15)))
    assert list(state.lines) == ["wan2"]


# -- the state file -------------------------------------------------------------------------------


def test_the_state_file_round_trips_and_keeps_its_windows(tmp_path: Path) -> None:
    path = tmp_path / "state" / "wan-rates.json"
    fake, state = agent(), SourceState("fw-1")
    now = T0
    poll(fake, state, now)
    for _ in range(int(MINUTES_KEEP / timedelta(minutes=5)) + 12):  # 25 hours, a read every 5 min
        now += timedelta(minutes=5)
        fake.tick(300, down=300 * 4000 // 8)
        poll(fake, state, now)
    write_state(path, WanState({"fw-1": state}), now)
    assert not list(path.parent.glob(".*.tmp"))  # written atomically
    assert path.read_text(encoding="utf-8").count("\n") == 1  # compact: one line, no indent
    wan1 = read_state(path).sources["fw-1"].lines["wan1"]
    assert all(now - at <= SAMPLES_KEEP for at, _, _ in wan1.samples)
    assert len(wan1.samples) == 13  # every 5 minutes for 60 minutes
    assert all(now - minute[0] <= MINUTES_KEEP for minute in wan1.minutes)
    assert len(wan1.minutes) == 24 * 12 + 1
    assert {(down, up) for _, down, up in wan1.samples} == {(4000.0, 0.0)}
    assert wan1.counters is not None
    assert wan1.counters.at == now
    assert wan1.capacity == (1000.0, 100.0)


def test_one_minute_averages() -> None:
    state = SourceState("fw-1", lines={})
    fake = agent()
    poll(fake, state, T0)
    for seconds, bits in ((15, 1000), (30, 3000), (45, 5000)):
        fake.tick(15, down=15 * bits // 8)
        poll(fake, state, T0 + timedelta(seconds=seconds))
    assert state.lines["wan1"].minutes == [(T0, 3000.0, 0.0, 3)]


def test_an_unreadable_state_file_starts_again(tmp_path: Path, caplog: Any) -> None:
    path = tmp_path / "wan-rates.json"
    path.write_text("{", encoding="utf-8")
    assert read_state(path).sources == {}
    path.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    assert read_state(path).sources == {}
    assert read_state(tmp_path / "missing.json").sources == {}


# -- the job --------------------------------------------------------------------------------------


def _config(tmp_path: Path, **snmp: Any) -> Config:
    """A configuration with one SNMP source, a network source and a source without SNMP."""
    settings = {
        "enabled": True,
        "port": 1161,
        "user": "arcilogger",
        "interfaces": ["wan1"],
        "interval_seconds": 15,
        **snmp,
    }
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path / "secrets"),
            "sources": [
                {"name": "fw-1", "type": "fortigate", "address": "172.20.0.1", "snmp": settings},
                {"name": "aps", "type": "generic", "address": "172.20.10.0/24"},
                {"name": "core-r1", "type": "mikrotik", "address": "172.20.0.2"},
            ],
        }
    )


def _store(tmp_path: Path) -> SecretStore:
    folder = tmp_path / "secrets"
    folder.mkdir(exist_ok=True)
    SecretStore.create_key(folder / "secret.key")
    store = SecretStore(folder / "secret.key", folder / "secrets.json")
    store.set(snmp_auth_key("fw-1"), AUTH_KEY)
    store.set(snmp_priv_key("fw-1"), PRIV_KEY)
    return store


def test_only_enabled_single_address_sources_are_polled(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (source,) = snmp_sources(config)
    assert (source.name, source.host, source.port, source.auth, source.priv) == (
        "fw-1",
        "172.20.0.1",
        1161,
        "sha256",
        "aes128",
    )
    assert snmp_sources(_config(tmp_path, enabled=False)) == []


@pytest.mark.parametrize(
    ("settings", "refused"),
    [
        ({"version": 2}, "extra_forbidden"),  # no v1/v2c: there is no version
        ({"community": "public"}, "extra_forbidden"),  # and no community string
        ({"auth": "none"}, "literal_error"),  # noAuthNoPriv
        ({"priv": "none"}, "literal_error"),  # authNoPriv
        ({"auth": "md5"}, "literal_error"),
        ({"priv": "des"}, "literal_error"),
        ({"port": 0}, "greater_than_equal"),
        ({"port": 65536}, "less_than_equal"),
        ({"interval_seconds": 9}, "greater_than_equal"),
        ({"interval_seconds": 61}, "less_than_equal"),
        ({"interfaces": []}, "snmp_setting_invalid"),
        ({"interfaces": ["a", "b", "c", "d", "e"]}, "too_long"),
        ({"interfaces": ["wan1", "wan1"]}, "snmp_setting_invalid"),
        ({"user": ""}, "snmp_setting_invalid"),
        ({"capacity_mbps": {"wan9": {"down": 10, "up": 1}}}, "snmp_setting_invalid"),
        ({"capacity_mbps": {"wan1": {"down": 0, "up": 1}}}, "greater_than_equal"),
    ],
)
def test_n1_refusals(tmp_path: Path, settings: dict[str, Any], refused: str) -> None:
    """The rules of N1, in the configuration model: refused when the configuration is read."""
    with pytest.raises(ValidationError) as error:
        _config(tmp_path, **settings)
    assert refused in str(error.value) or refused in {e["type"] for e in error.value.errors()}
    assert str(settings) not in str(error.value)  # hide_input_in_errors


def test_snmp_on_a_network_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="snmp_source_is_network"):
        Config.model_validate(
            {
                "data_dir": str(tmp_path),
                "secrets_dir": str(tmp_path),
                "sources": [
                    {
                        "name": "aps",
                        "type": "generic",
                        "address": "172.20.10.0/24",
                        "snmp": {"enabled": True, "user": "u", "interfaces": ["wan1"]},
                    }
                ],
            }
        )


def test_snmp_on_an_ipv6_address_is_refused(tmp_path: Path) -> None:
    """The UDP transport is IPv4 only, as the browser's ``source_ipv6_unsupported``."""
    with pytest.raises(ValidationError, match="source_ipv6_unsupported"):
        Config.model_validate(
            {
                "data_dir": str(tmp_path),
                "secrets_dir": str(tmp_path),
                "sources": [
                    {
                        "name": "fw-6",
                        "type": "fortigate",
                        "address": "2001:db8::1",
                        "snmp": {"enabled": True, "user": "u", "interfaces": ["wan1"]},
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "settings",
    [
        {"auth": "sha1", "priv": "aes256"},
        {"auth": "sha512", "port": 65535, "interval_seconds": 60},
        {"interfaces": ["a", "b", "c", "d"], "interval_seconds": 10, "port": 1},
        {"capacity_mbps": {"wan1": {"down": 1000, "up": 100}}},
    ],
)
def test_n1_bounds_and_choices_accepted(tmp_path: Path, settings: dict[str, Any]) -> None:
    (source,) = snmp_sources(_config(tmp_path, **settings))
    assert source.name == "fw-1"


@pytest.mark.parametrize("keys", [None, ("short", "privkeyprivkey"), ("x" * 65, "privkeyprivkey")])
def test_a_missing_or_bad_key_is_reported_and_polled_without_a_request(
    tmp_path: Path, layout: Layout, keys: tuple[str, str] | None
) -> None:
    store = _store(tmp_path)
    if keys is None:
        store.delete(snmp_priv_key("fw-1"))
    else:
        store.set(snmp_auth_key("fw-1"), keys[0])
    fake = agent()
    report = snmp_job.poll_due(layout, _config(tmp_path), now=T0, sessions=None, client=fake)
    assert report is not None
    assert report.refused == {"fw-1": "snmp_secret_missing"}
    assert (fake.walks, fake.gets) == (0, [])
    line = read_state(layout.wan_rates).sources["fw-1"].lines["wan1"]
    assert line.status == "auth_failed"


def test_the_job_polls_due_sources_and_writes_the_file(tmp_path: Path, layout: Layout) -> None:
    _store(tmp_path)
    config, fake = _config(tmp_path), agent()
    report = snmp_job.poll_due(layout, config, now=T0, sessions=None, client=fake)
    assert report is not None
    assert report.polled == ["fw-1"]
    assert fake.walks == 1
    data = json.loads(layout.wan_rates.read_text(encoding="utf-8"))
    assert data["schema"] == "arcilogger.wan-rates/1"
    assert list(data["sources"]["fw-1"]["lines"]) == ["wan1"]
    # not due 5 s later; due again after its interval (2.5 s early, the job runs every 5 s)
    report = snmp_job.poll_due(
        layout, config, now=T0 + timedelta(seconds=5), sessions=None, client=fake
    )
    assert report is not None
    assert report.polled == []
    fake.tick(15, down=1_875_000)
    report = snmp_job.poll_due(
        layout, config, now=T0 + timedelta(seconds=12.5), sessions=None, client=fake
    )
    assert report is not None
    assert report.polled == ["fw-1"]
    assert read_state(layout.wan_rates).sources["fw-1"].lines["wan1"].samples


def test_nothing_to_do_without_snmp(tmp_path: Path, layout: Layout) -> None:
    config = _config(tmp_path, enabled=False)
    assert snmp_job.poll_due(layout, config, now=T0, sessions=None, client=agent()) is None
    assert not layout.wan_rates.exists()


def test_switching_snmp_off_marks_the_lines_disabled(tmp_path: Path, layout: Layout) -> None:
    _store(tmp_path)
    fake = agent()
    snmp_job.poll_due(layout, _config(tmp_path), now=T0, sessions=None, client=fake)
    snmp_job.poll_due(layout, _config(tmp_path, enabled=False), now=T0, sessions=None, client=fake)
    state = read_state(layout.wan_rates).sources["fw-1"]
    assert {line.status for line in state.lines.values()} == {"disabled"}
    removed = Config.model_validate(
        {"data_dir": str(tmp_path / "data"), "secrets_dir": str(tmp_path / "secrets")}
    )
    snmp_job.poll_due(layout, removed, now=T0, sessions=None, client=fake)
    assert read_state(layout.wan_rates).sources == {}


def test_idle_while_nothing_is_due_or_left_to_clear(tmp_path: Path, layout: Layout) -> None:
    """The worker writes no ``job_runs`` row for an idle check (most sites have no SNMP)."""
    _store(tmp_path)
    on, off, fake = _config(tmp_path), _config(tmp_path, enabled=False), agent()
    assert snmp_job.idle(layout, off, now=T0)
    assert not snmp_job.idle(layout, on, now=T0)  # never polled
    snmp_job.poll_due(layout, on, now=T0, sessions=None, client=fake)
    assert snmp_job.idle(layout, on, now=T0 + timedelta(seconds=5))
    assert not snmp_job.idle(layout, on, now=T0 + timedelta(seconds=12.5))
    assert not snmp_job.idle(layout, off, now=T0)  # its lines are to be marked disabled
    snmp_job.poll_due(layout, off, now=T0, sessions=None, client=fake)
    assert snmp_job.idle(layout, off, now=T0 + timedelta(hours=1))
    removed = Config.model_validate(
        {"data_dir": str(tmp_path / "data"), "secrets_dir": str(tmp_path / "secrets")}
    )
    assert not snmp_job.idle(layout, removed, now=T0)  # its state is to be dropped
    snmp_job.poll_due(layout, removed, now=T0, sessions=None, client=fake)
    assert snmp_job.idle(layout, removed, now=T0)


def _snmp_alerts(session: Session) -> list[Alert]:
    return list(session.query(Alert).filter(Alert.rule == "R20-snmp").order_by(Alert.id))


def test_r20_snmp_after_ten_minutes_and_resolved_by_a_good_read(
    tmp_path: Path, layout: Layout, db_session_factory: sessionmaker[Session]
) -> None:
    _store(tmp_path)
    config, fake = _config(tmp_path), agent()
    snmp_job.poll_due(layout, config, now=T0, sessions=db_session_factory, client=fake)
    fake.error = "timeout"
    now = T0
    while now - T0 < timedelta(minutes=10):
        now += timedelta(seconds=15)
        report = snmp_job.poll_due(
            layout, config, now=now, sessions=db_session_factory, client=fake
        )
        assert report is not None
        if now - T0 < timedelta(minutes=10):
            assert report.opened == []
    assert report is not None
    assert report.opened == ["fw-1"]
    with db_session_factory() as session:
        (alert,) = _snmp_alerts(session)
        assert (alert.severity, alert.state, alert.dedup_key) == ("low", "open", "R20-snmp:fw-1")
        assert alert.details["lines"] == {"wan1": "timeout"}
        assert "wan1: yanıt yok" in alert.title
    fake.error = None
    now += timedelta(seconds=15)
    report = snmp_job.poll_due(layout, config, now=now, sessions=db_session_factory, client=fake)
    assert report is not None
    assert report.resolved == ["fw-1"]
    with db_session_factory() as session:
        (alert,) = _snmp_alerts(session)
        assert alert.state == "resolved"
        assert alert.details[AUTO_RESOLVED]["reason"] == "read_again"


def test_a_source_that_never_answered_alerts_after_ten_minutes(
    tmp_path: Path, layout: Layout, db_session_factory: sessionmaker[Session]
) -> None:
    _store(tmp_path)
    config, fake = _config(tmp_path), agent()
    fake.error = "auth_failed"
    snmp_job.poll_due(layout, config, now=T0, sessions=db_session_factory, client=fake)
    late = T0 + timedelta(minutes=10)
    report = snmp_job.poll_due(layout, config, now=late, sessions=db_session_factory, client=fake)
    assert report is not None
    assert report.opened == ["fw-1"]
    # switching SNMP off resolves it: the source is no longer polled
    off = _config(tmp_path, enabled=False)
    report = snmp_job.poll_due(layout, off, now=late, sessions=db_session_factory, client=fake)
    assert report is not None
    assert report.resolved == ["fw-1"]
    with db_session_factory() as session:
        (alert,) = _snmp_alerts(session)
        assert alert.details[AUTO_RESOLVED]["reason"] == "not_polled"


def _fails_ten_minutes_after(
    layout: Layout, config: Config, fake: FakeAgent, start: datetime
) -> None:
    """A failing line opens nothing before ``start`` + 10 minutes, and is reported then."""
    for minutes in (0, 5, 9.5):
        now = start + timedelta(minutes=minutes)
        report = snmp_job.poll_due(layout, config, now=now, sessions=None, client=fake)
        assert report is not None
        assert report.failing == [], minutes
    late = start + timedelta(minutes=10)
    report = snmp_job.poll_due(layout, config, now=late, sessions=None, client=fake)
    assert report is not None
    assert report.failing == ["fw-1"]


def test_switching_snmp_on_again_starts_the_ten_minutes_again(
    tmp_path: Path, layout: Layout
) -> None:
    """A good read from before SNMP was switched off does not make a new failure late."""
    _store(tmp_path)
    fake = agent()
    snmp_job.poll_due(layout, _config(tmp_path), now=T0, sessions=None, client=fake)
    off = T0 + timedelta(minutes=1)
    snmp_job.poll_due(layout, _config(tmp_path, enabled=False), now=off, sessions=None, client=fake)
    fake.error = "timeout"
    _fails_ten_minutes_after(layout, _config(tmp_path), fake, T0 + timedelta(hours=1))


def test_a_new_interface_has_its_own_ten_minutes(tmp_path: Path, layout: Layout) -> None:
    _store(tmp_path)
    fake = agent()
    snmp_job.poll_due(layout, _config(tmp_path), now=T0, sessions=None, client=fake)
    added = _config(tmp_path, interfaces=["wan1", "wan9"])  # wan9: no_such_interface
    fake.tick(15)
    _fails_ten_minutes_after(layout, added, fake, T0 + timedelta(hours=1))
    line = read_state(layout.wan_rates).sources["fw-1"].lines["wan9"]
    assert line.status == "no_such_interface"


class _Broken(FakeAgent):
    """A client that fails with something other than :class:`SnmpError` for one host."""

    async def get(self, target: Target, oids: Sequence[str]) -> dict[str, Value]:
        if target.host == "172.20.0.2":
            raise RuntimeError("not an SNMP error")
        return await super().get(target, oids)


def test_one_source_failing_otherwise_does_not_stop_the_others(
    tmp_path: Path, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    store.set(snmp_auth_key("core-r1"), AUTH_KEY)
    store.set(snmp_priv_key("core-r1"), PRIV_KEY)
    data = _config(tmp_path).model_dump(mode="json")
    data["sources"][2]["snmp"] = data["sources"][0]["snmp"]
    config = Config.model_validate(data)
    fake = _Broken({1: Interface("wan1", 10_000, 20_000)})
    report = snmp_job.poll_due(layout, config, now=T0, sessions=None, client=fake)
    assert report is not None
    assert report.polled == ["fw-1", "core-r1"]
    sources = read_state(layout.wan_rates).sources
    assert sources["fw-1"].lines["wan1"].status == "ok"
    assert sources["core-r1"].lines["wan1"].status == "timeout"
    assert "SNMP poll of core-r1 failed: RuntimeError" in caplog.text


def test_a_state_file_that_cannot_be_written_is_written_at_the_next_poll(
    tmp_path: Path, layout: Layout, caplog: pytest.LogCaptureFixture, monkeypatch: Any
) -> None:
    _store(tmp_path)
    config, fake = _config(tmp_path), agent()

    def locked(*_args: Any) -> None:
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(snmp_job, "write_state", locked)
    report = snmp_job.poll_due(layout, config, now=T0, sessions=None, client=fake)
    assert report is not None
    assert report.polled == ["fw-1"]
    assert not layout.wan_rates.exists()
    assert "wan-rates.json cannot be written now" in caplog.text
    monkeypatch.setattr(snmp_job, "write_state", write_state)
    later = T0 + timedelta(seconds=15)
    snmp_job.poll_due(layout, config, now=later, sessions=None, client=fake)
    assert read_state(layout.wan_rates).sources["fw-1"].polled_at == later


# -- secrets --------------------------------------------------------------------------------------


def test_the_keys_never_reach_a_log_line_the_file_or_an_alert(
    tmp_path: Path,
    layout: Layout,
    db_session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _store(tmp_path)
    config, fake = _config(tmp_path), agent()
    errors: tuple[Status | None, ...] = (None, "timeout", "auth_failed")
    for error in errors:
        fake.error = error
        for minute in range(0, 12, 3):
            now = T0 + timedelta(minutes=minute)
            snmp_job.poll_due(layout, config, now=now, sessions=db_session_factory, client=fake)
    assert AUTH_KEY not in repr(TARGET)
    assert PRIV_KEY not in repr(TARGET)
    assert AUTH_KEY not in str(SnmpError("auth_failed", "WrongDigest"))
    written = layout.wan_rates.read_text(encoding="utf-8")
    with db_session_factory() as session:
        alerts = json.dumps([[a.title, a.details] for a in _snmp_alerts(session)])
    for text in (caplog.text, written, alerts):
        assert AUTH_KEY not in text
        assert PRIV_KEY not in text


def test_the_alert_texts_name_the_source_and_the_reasons_only() -> None:
    """``R20-snmp`` in the e-mail catalogue: the source, a count and the reasons; never an
    interface name (a configuration value), a user name or a key."""
    details = {
        "source": "fw-1",
        "lines": {"wan1": "timeout", "isp-b": "auth_failed"},
        "minutes": 10,
        "title_en": "x",
    }
    found = texts.facts("R20-snmp", details)
    assert found is not None
    tr, en = found
    assert "fw-1" in tr
    assert "2 internet" in tr
    assert "yanıt yok" in tr
    assert "kimlik doğrulama başarısız" in tr
    assert en.startswith("The speed of 2 internet line(s) of fw-1")
    for text in (tr, en):
        assert "wan1" not in text
        assert "isp-b" not in text
    assert texts.RULES["R20-snmp"].name_en == "Internet line speed cannot be read"
    assert "Test connection" in texts.runbook("R20-snmp")[1]
    assert texts.facts("R20-snmp", {"source": "fw-1", "lines": {}, "minutes": 10}) is None
